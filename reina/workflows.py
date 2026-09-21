"""ComfyUI の API フォーマット・ワークフローを組み立てる。

ノード名は ComfyUI / ComfyUI-GGUF のバージョンで変わることがあるため、
`reina doctor` で実在を確認してから使うこと。
どうしても合わない場合は ComfyUI から "Export (API)" したJSONを
workflows/ に置き、`--template` で渡せば placeholder 差し替えで動く。
"""

from __future__ import annotations

import copy
import json
import random
from pathlib import Path
from typing import Any

Graph = dict[str, dict[str, Any]]

# 参照画像つきエンコーダの候補（先に見つかったものを使う）
EDIT_ENCODER_CANDIDATES = (
    "TextEncodeQwenImageEditPlus",
    "TextEncodeQwenImageEdit",
)

MAX_SEED = 2**32 - 1


def random_seed() -> int:
    return random.randint(0, MAX_SEED)


class WorkflowBuilder:
    """models 設定と生成パラメータから API 形式のグラフを作る。"""

    def __init__(self, models: dict[str, Any], defaults: dict[str, Any], loras: list[dict[str, Any]] | None = None):
        self.models = models
        self.defaults = defaults
        self.loras = loras or []

    # -- 共通パーツ ---------------------------------------------------------

    def _loaders(self, graph: Graph) -> tuple[list, list, list]:
        """UNet / CLIP / VAE ローダを graph に足し、それぞれの出力参照を返す。"""
        # .gguf は ComfyUI-GGUF のローダ、.safetensors は ComfyUI 標準のローダ
        name = str(self.models["unet_gguf"])
        if name.lower().endswith(".gguf"):
            graph["1"] = {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": name}}
        else:
            graph["1"] = {
                "class_type": "UNETLoader",
                "inputs": {
                    "unet_name": name,
                    "weight_dtype": self.models.get("weight_dtype", "default"),
                },
            }
        model_ref: list = ["1", 0]

        for index, lora in enumerate(self.loras):
            node_id = f"1{index + 1}"
            graph[node_id] = {
                "class_type": "LoraLoaderModelOnly",
                "inputs": {
                    "model": model_ref,
                    "lora_name": lora["name"],
                    "strength_model": float(lora.get("strength", 1.0)),
                },
            }
            model_ref = [node_id, 0]

        clip_class = "CLIPLoaderGGUF" if self.models.get("text_encoder_is_gguf") else "CLIPLoader"
        clip_inputs: dict[str, Any] = {
            "clip_name": self.models["text_encoder"],
            "type": self.models.get("clip_type", "qwen_image"),
        }
        graph["2"] = {"class_type": clip_class, "inputs": clip_inputs}

        graph["3"] = {"class_type": "VAELoader", "inputs": {"vae_name": self.models["vae"]}}

        graph["4"] = {
            "class_type": "ModelSamplingAuraFlow",
            "inputs": {"model": model_ref, "shift": float(self.defaults["shift"])},
        }
        return ["4", 0], ["2", 0], ["3", 0]

    def _sampler_and_output(
        self,
        graph: Graph,
        model_ref: list,
        positive: list,
        negative: list,
        latent: list,
        vae_ref: list,
        params: dict[str, Any],
    ) -> Graph:
        graph["20"] = {
            "class_type": "KSampler",
            "inputs": {
                "model": model_ref,
                "positive": positive,
                "negative": negative,
                "latent_image": latent,
                "seed": int(params["seed"]),
                "steps": int(params["steps"]),
                "cfg": float(params["cfg"]),
                "sampler_name": params["sampler"],
                "scheduler": params["scheduler"],
                "denoise": float(params["denoise"]),
            },
        }
        graph["21"] = {"class_type": "VAEDecode", "inputs": {"samples": ["20", 0], "vae": vae_ref}}
        graph["22"] = {
            "class_type": "SaveImage",
            "inputs": {"images": ["21", 0], "filename_prefix": params.get("prefix", "reina")},
        }
        return graph

    def _params(self, **overrides: Any) -> dict[str, Any]:
        params = dict(self.defaults)
        params.update({k: v for k, v in overrides.items() if v is not None})
        params.setdefault("seed", random_seed())
        return params

    # -- text-to-image ------------------------------------------------------

    def text_to_image(self, prompt: str, negative: str = "", **overrides: Any) -> Graph:
        params = self._params(**overrides)
        graph: Graph = {}
        model_ref, clip_ref, vae_ref = self._loaders(graph)

        graph["10"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": clip_ref, "text": prompt}}
        graph["11"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": clip_ref, "text": negative}}
        graph["12"] = {
            "class_type": "EmptySD3LatentImage",
            "inputs": {
                "width": int(params["width"]),
                "height": int(params["height"]),
                "batch_size": int(params["batch_size"]),
            },
        }
        return self._sampler_and_output(
            graph, model_ref, ["10", 0], ["11", 0], ["12", 0], vae_ref, params
        )

    # -- 参照画像つき（image edit） -----------------------------------------

    def reference_to_image(
        self,
        prompt: str,
        reference_images: list[str],
        negative: str = "",
        encoder_class: str = EDIT_ENCODER_CANDIDATES[0],
        **overrides: Any,
    ) -> Graph:
        """参照画像（最大3枚）を条件に画像を生成する。

        reference_images は ComfyUI の input/ 配下の名前
        (ComfyClient.upload_image の戻り値) を渡す。
        """
        if not reference_images:
            raise ValueError("参照画像が1枚以上必要です")
        if len(reference_images) > 3:
            raise ValueError("参照画像は最大3枚です")

        params = self._params(**overrides)
        params["denoise"] = float(overrides.get("denoise") or self.defaults.get("reference_denoise", 1.0))
        megapixels = float(self.defaults.get("reference_megapixels", 1.0))

        graph: Graph = {}
        model_ref, clip_ref, vae_ref = self._loaders(graph)

        scaled_refs: list[list] = []
        for index, name in enumerate(reference_images):
            load_id = f"3{index}1"
            scale_id = f"3{index}2"
            graph[load_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
            graph[scale_id] = {
                "class_type": "ImageScaleToTotalPixels",
                "inputs": {
                    "image": [load_id, 0],
                    "upscale_method": "lanczos",
                    "megapixels": megapixels,
                },
            }
            scaled_refs.append([scale_id, 0])

        pos_inputs: dict[str, Any] = {"clip": clip_ref, "prompt": prompt, "vae": vae_ref}
        neg_inputs: dict[str, Any] = {"clip": clip_ref, "prompt": negative, "vae": vae_ref}
        for index, ref in enumerate(scaled_refs):
            pos_inputs[f"image{index + 1}"] = ref
            neg_inputs[f"image{index + 1}"] = ref

        graph["10"] = {"class_type": encoder_class, "inputs": pos_inputs}
        graph["11"] = {"class_type": encoder_class, "inputs": neg_inputs}

        # 1枚目の参照画像を初期 latent にする（構図を引き継ぐ）
        if params["denoise"] < 1.0:
            graph["12"] = {
                "class_type": "VAEEncode",
                "inputs": {"pixels": scaled_refs[0], "vae": vae_ref},
            }
            latent: list = ["12", 0]
        else:
            graph["12"] = {
                "class_type": "EmptySD3LatentImage",
                "inputs": {
                    "width": int(params["width"]),
                    "height": int(params["height"]),
                    "batch_size": int(params["batch_size"]),
                },
            }
            latent = ["12", 0]

        return self._sampler_and_output(
            graph, model_ref, ["10", 0], ["11", 0], latent, vae_ref, params
        )


# -- テンプレート差し替え ----------------------------------------------------

PLACEHOLDERS = {
    "prompt": "__PROMPT__",
    "negative": "__NEGATIVE__",
    "seed": "__SEED__",
    "image": "__IMAGE__",
    "width": "__WIDTH__",
    "height": "__HEIGHT__",
    "steps": "__STEPS__",
    "cfg": "__CFG__",
    "prefix": "__PREFIX__",
}


def patch_template(template_path: str | Path, values: dict[str, Any]) -> Graph:
    """ComfyUI から "Export (API)" したJSONの placeholder を実値に差し替える。

    テンプレート側のテキスト欄に __PROMPT__ などを書いておく運用。
    自前グラフのノード名がComfyUIのバージョンと合わない時の確実な逃げ道。
    """
    graph: Graph = json.loads(Path(template_path).read_text(encoding="utf-8"))
    if "prompt" in graph and "nodes" not in graph:
        pass  # すでに API フォーマット
    elif "nodes" in graph:
        raise ValueError(
            "UIフォーマットのJSONです。ComfyUI の設定で 'Export (API)' を有効にして"
            "書き出したJSONを指定してください。"
        )

    patched = copy.deepcopy(graph)
    for node in patched.values():
        inputs = node.get("inputs", {})
        for key, current in list(inputs.items()):
            if not isinstance(current, str):
                continue
            for name, token in PLACEHOLDERS.items():
                if token not in current or name not in values:
                    continue
                value = values[name]
                if current.strip() == token:
                    inputs[key] = value  # 型を保つ（seed / width など）
                else:
                    inputs[key] = current.replace(token, str(value))
                current = inputs[key]
                if not isinstance(current, str):
                    break
    return patched
