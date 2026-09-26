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
# TextEncodeQwenImageEditPlus は image1〜image3 の3枚固定で、positive/negative を
# 別々に2回呼ぶ。各画像に "Picture N:" のラベルが付く。
# TextEncodeQwenImage21 は Qwen-Image 2.1 用の新ノード（ComfyUI v0.37.0〜）で、
# 1回の呼び出しで positive/negative 両方を出し、参照画像は最大16枚、ラベルは <imageN>。
# ただし UC GGUF + パッチした ComfyUI-GGUF の構成では、同じシード・同じ参照画像でも
# 21 だと別人になり、EditPlus だと本人になることを実機で確認したため EditPlus を優先する。
# 4枚以上使いたいときは --encoder TextEncodeQwenImage21 で明示する。
EDIT_ENCODER_CANDIDATES = (
    "TextEncodeQwenImageEditPlus",
    "TextEncodeQwenImage21",
    "TextEncodeQwenImageEdit",
)

# 1ノードで positive/negative を同時に出すタイプ（画像入力名も image_1 形式）
COMBINED_ENCODERS = ("TextEncodeQwenImage21",)

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

        # strength 0 は「適用しない」という指定なので、ノード自体を作らない。
        # LoraLoaderModelOnly を挟むと model.clone()+patch が走る（strength=0 でも）。
        # GGUF ローダ由来のモデルはこの clone/patch 経路と相性問題が出ることがあるため、
        # 不要なら GGUF ローダの出力をそのまま使う方が安全
        active_loras = [lora for lora in self.loras if float(lora.get("strength", 1.0)) != 0]
        for index, lora in enumerate(active_loras):
            # "12" などの短い ID は他の固定ノード（latent 等）と衝突するので、
            # 十分に桁を離しておく（LoRA を何個使っても衝突しない）
            node_id = f"1{index + 1:02d}0"
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
        use_reference_vae: bool = True,
        **overrides: Any,
    ) -> Graph:
        """参照画像を条件に画像を生成する。

        reference_images は ComfyUI の input/ 配下の名前
        (ComfyClient.upload_image の戻り値) を渡す。

        use_reference_vae: TextEncodeQwenImage21 に vae を渡すか。
        渡すと画像を reference_latents としてVAEエンコードし、そちらを
        本命の同一性経路として使う（vision-language 側の画像トークンは
        使われなくなる: ノード実装が keep_vision=(ref_latents が空) を見る
        ため）。GGUF ローダがこの Qwen-Image 2.1 の reference_latents 経路
        に対応していないと、画像が黙って無視され「参照が一切効かない」
        症状になりうる。False にすると vae を渡さず、代わりに
        vision-language の画像トークン経路（古くからある、モデル側の
        対応状況に依存しにくい経路）で同一性を伝える。
        """
        if not reference_images:
            raise ValueError("参照画像が1枚以上必要です")

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

        if encoder_class in COMBINED_ENCODERS:
            # 新ノード: 1回で positive/negative 両方を出す。
            # 画像は可変入力(COMFY_AUTOGROW_V3)で、実行時は
            # execute(images={"image_1": ..., "image_2": ...}) という形で
            # 1個の dict にまとめて渡す必要がある
            # （image_1 を直接トップレベルのキーにすると
            #  "unexpected keyword argument 'image_1'" で弾かれる）。
            images: dict[str, Any] = {
                f"image_{index + 1}": ref for index, ref in enumerate(scaled_refs)
            }
            inputs: dict[str, Any] = {
                "clip": clip_ref,
                "prompt": prompt,
                "negative_prompt": negative,
                "resolution": int(self.defaults.get("reference_resolution", 1024)),
                "images": images,
            }
            if use_reference_vae:
                inputs["vae"] = vae_ref
            graph["10"] = {"class_type": encoder_class, "inputs": inputs}
            positive_out, negative_out = ["10", 0], ["10", 1]
        else:
            pos_inputs: dict[str, Any] = {"clip": clip_ref, "prompt": prompt, "vae": vae_ref}
            neg_inputs: dict[str, Any] = {"clip": clip_ref, "prompt": negative, "vae": vae_ref}
            for index, ref in enumerate(scaled_refs):
                pos_inputs[f"image{index + 1}"] = ref
                neg_inputs[f"image{index + 1}"] = ref
            graph["10"] = {"class_type": encoder_class, "inputs": pos_inputs}
            graph["11"] = {"class_type": encoder_class, "inputs": neg_inputs}
            positive_out, negative_out = ["10", 0], ["11", 0]

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
            graph, model_ref, positive_out, negative_out, latent, vae_ref, params
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
