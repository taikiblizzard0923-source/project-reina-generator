"""MiniMax H3 で音声付き動画を作る API 形式のワークフロー。

ComfyUI 同梱の公式テンプレート（video_minimax_h3_i2v / video_minimax_h3_r2v）と
同じ繋ぎ方にしてある:
    UNETLoader → (turbo LoRA) → BasicScheduler / BasicGuider
    CLIPLoader(type=minimax) + 映像VAE (+ 音声VAE) → MiniMaxH3ImageToVideo / ReferenceToVideo
    → SamplerCustomAdvanced(res_multistep) → VAEDecode + VAEDecodeAudio → CreateVideo → SaveVideo
cfg は無い（BasicGuider）。音声はプロンプトから同時に生成される。
"""

from __future__ import annotations

import math
from typing import Any

from .workflows import Graph, autogrow_inputs

FPS = 24
# H3 の生成サイズの上限（短辺 768 / 長辺 1344）
MAX_SHORT, MAX_LONG = 768, 1344
MAX_REFERENCE_IMAGES = 9


def frame_count(seconds: float) -> int:
    """秒数を H3 が受け付けるフレーム数（17k+5）に切り上げる。公式テンプレートと同じ式。"""
    frames = max(5, round(seconds * FPS))
    return frames + (5 - frames % 17) % 17


def video_size(aspect_w: float, aspect_h: float, megapixels: float, multiple: int = 32) -> tuple[int, int]:
    """縦横比と画素数から 32 の倍数のサイズを出す（ComfyUI の ResolutionSelector と同じ切り上げ）。"""
    ratio = aspect_w / aspect_h
    width = math.sqrt(megapixels * 1_000_000 * ratio)
    height = width / ratio
    scale = min(1.0, MAX_SHORT / min(width, height), MAX_LONG / max(width, height))
    width, height = width * scale, height * scale
    return (
        max(multiple, math.ceil(width / multiple) * multiple),
        max(multiple, math.ceil(height / multiple) * multiple),
    )


def reference_prompt(scene: str, count: int) -> str:
    """ref2va 用の指示文。参照は接続順の <Picture N> タグで指す（公式テンプレートの書き方）。"""
    tags = [f"<Picture {i}>" for i in range(1, count + 1)]
    faces = tags[0] if count == 1 else ", ".join(tags[:-1]) + " and " + tags[-1]
    verb = "shows" if count == 1 else "show"
    return (
        f"{faces} {verb} one and the same person, who appears only once in the video. "
        f"Use {faces} only for the person's face and identity, "
        f"not for the clothing, pose or background. {scene.strip()}"
    )


class VideoWorkflowBuilder:
    def __init__(self, video: dict[str, Any]):
        self.models = video["models"]
        self.video = video

    def _base(self, graph: Graph, mode: str, steps: int | None, turbo: bool | None) -> tuple[list, list, list, list, int]:
        turbo = self.video.get("turbo", True) if turbo is None else turbo
        graph["1"] = {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": self.models[mode], "weight_dtype": "default"},
        }
        model_ref: list = ["1", 0]
        if turbo:
            graph["2"] = {
                "class_type": "LoraLoaderModelOnly",
                "inputs": {
                    "model": model_ref,
                    "lora_name": self.models[f"{mode}_turbo_lora"],
                    "strength_model": 1.0,
                },
            }
            model_ref = ["2", 0]
        if steps is None:
            steps = int(self.video["turbo_steps"] if turbo else self.video["steps"])
        graph["3"] = {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": self.models["text_encoder"], "type": "minimax", "device": "default"},
        }
        graph["4"] = {"class_type": "VAELoader", "inputs": {"vae_name": self.models["video_vae"]}}
        graph["5"] = {"class_type": "VAELoader", "inputs": {"vae_name": self.models["audio_vae"]}}
        return model_ref, ["3", 0], ["4", 0], ["5", 0], steps

    def _sample_and_save(
        self, graph: Graph, model_ref: list, scheduler: str, steps: int, seed: int, prefix: str
    ) -> Graph:
        graph["11"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed)}}
        graph["12"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": self.video["sampler"]}}
        graph["13"] = {
            "class_type": "BasicScheduler",
            "inputs": {"model": model_ref, "scheduler": scheduler, "steps": int(steps), "denoise": 1.0},
        }
        graph["14"] = {"class_type": "BasicGuider", "inputs": {"model": model_ref, "conditioning": ["10", 0]}}
        graph["15"] = {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["11", 0],
                "guider": ["14", 0],
                "sampler": ["12", 0],
                "sigmas": ["13", 0],
                "latent_image": ["10", 1],
            },
        }
        # 音声と映像は1つの latent に詰まっていて、それぞれの VAE が自分の分を取り出す
        graph["20"] = {"class_type": "VAEDecode", "inputs": {"samples": ["15", 0], "vae": ["4", 0]}}
        graph["21"] = {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["15", 0], "vae": ["5", 0]}}
        graph["22"] = {
            "class_type": "CreateVideo",
            "inputs": {"images": ["20", 0], "audio": ["21", 0], "fps": float(FPS)},
        }
        graph["23"] = {
            "class_type": "SaveVideo",
            "inputs": {"video": ["22", 0], "filename_prefix": prefix, "format": "auto", "format.codec": "auto"},
        }
        return graph

    def image_to_video(
        self,
        prompt: str,
        first_frame: str,
        width: int,
        height: int,
        seconds: float,
        seed: int,
        steps: int | None = None,
        turbo: bool | None = None,
        prefix: str = "reina/video",
    ) -> Graph:
        """静止画を最初のフレームにして動かす（fl2va）。first_frame は ComfyUI input/ の名前。"""
        graph: Graph = {}
        model_ref, clip_ref, vae_ref, _, steps = self._base(graph, "fl2va", steps, turbo)
        graph["6"] = {"class_type": "LoadImage", "inputs": {"image": first_frame}}
        graph["10"] = {
            "class_type": "MiniMaxH3ImageToVideo",
            "inputs": {
                "clip": clip_ref,
                "vae": vae_ref,
                "first_frame": ["6", 0],
                "prompt": prompt,
                "width": int(width),
                "height": int(height),
                "length": frame_count(seconds),
            },
        }
        return self._sample_and_save(graph, model_ref, self.video["scheduler_i2v"], steps, seed, prefix)

    def reference_to_video(
        self,
        prompt: str,
        references: list[str],
        width: int,
        height: int,
        seconds: float,
        seed: int,
        steps: int | None = None,
        turbo: bool | None = None,
        prefix: str = "reina/video",
    ) -> Graph:
        """参照写真の人物が出る動画を作る（ref2va）。references は ComfyUI input/ の名前。"""
        if not references:
            raise ValueError("参照画像が1枚以上必要です")
        if len(references) > MAX_REFERENCE_IMAGES:
            raise ValueError(f"参照画像は {MAX_REFERENCE_IMAGES} 枚までです")
        graph: Graph = {}
        model_ref, clip_ref, vae_ref, audio_vae_ref, steps = self._base(graph, "ref2va", steps, turbo)
        loaded = []
        for index, name in enumerate(references):
            node_id = f"6{index}"
            graph[node_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
            loaded.append([node_id, 0])
        graph["10"] = {
            "class_type": "MiniMaxH3ReferenceToVideo",
            "inputs": {
                "clip": clip_ref,
                "vae": vae_ref,
                "audio_vae": audio_vae_ref,
                **autogrow_inputs("ref_images", "ref_image_", loaded),
                "prompt": prompt,
                "width": int(width),
                "height": int(height),
                "length": frame_count(seconds),
                "ref_image_size": self.video.get("ref_image_size", "match"),
            },
        }
        return self._sample_and_save(graph, model_ref, self.video["scheduler_r2v"], steps, seed, prefix)
