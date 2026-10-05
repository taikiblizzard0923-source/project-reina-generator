"""MiniMax H3 で音声付き動画を作る API 形式のワークフロー。

ComfyUI 同梱の公式テンプレート（video_minimax_h3_i2v / video_minimax_h3_r2v）と、
高速化 LoRA の配布元（ModelTC/Minimax-H3-Turbo）の ComfyUI ワークフローと同じ繋ぎ方:
    UNETLoader → (turbo LoRA) → MiniMaxH3SigmaShift → BasicScheduler / BasicGuider
    CLIPLoader(type=minimax) + 映像VAE (+ 音声VAE) → MiniMaxH3ImageToVideo / ReferenceToVideo
    → SamplerCustomAdvanced（高速化あり euler / なし res_multistep）→ VAEDecode + VAEDecodeAudio → CreateVideo → SaveVideo
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


# 指示文は H3 / Turbo の学習時の書式に合わせる（ModelTC/Minimax-H3-Turbo の
# ComfyUI ワークフローと COMFYUI_SETUP_AND_INFERENCE.md の例）。
# セリフは <d>[言語] 本文</d>、音は overall_soundscape、BGM は non_diegetic_music に書く。
ORDINALS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth")


def _dialogue(say: str | None, lang: str) -> str:
    return f" <Subject 1> says <d>[{lang}] {say.strip()}</d>" if say else ""


def _soundscape(sound: str | None, voice: str | None, say: str | None) -> str:
    parts = []
    if say:
        parts.append(f"<Subject 1> speaks in {voice.strip()}." if voice else "<Subject 1> speaks clearly.")
    parts.append(sound.strip() if sound else "Natural ambient sound that matches the scene.")
    return " ".join(parts)


def reference_prompt(
    scene: str,
    count: int,
    say: str | None = None,
    voice: str | None = None,
    sound: str | None = None,
    music: str | None = None,
    lang: str = "English",
) -> str:
    """ref2va 用。参照写真はすべて同じ1人（<Subject 1>）として定義し、顔だけを保持させる。"""
    which = [ORDINALS[i] for i in range(count)]
    images = (
        f"{which[0]} reference image" if count == 1
        else ", ".join(which[:-1]) + f" and {which[-1]} reference images"
    )
    scene = scene.strip().rstrip(".")
    return (
        "subject_definitions:\n"
        f"<Subject 1> is the person shown in the {images}: one and the same person "
        "seen from different angles.\n\n"
        "summary:\n"
        f"[reference generation] The target video shows <Subject 1>: {scene}. "
        "The reference images guide only <Subject 1>'s face and identity.\n\n"
        "retention_analysis:\n"
        "<Subject 1> (appears in [Shot 1]): fully_preserved - the facial features and identity "
        "are retained; clothing, hairstyle and surroundings follow the description, "
        "not the reference images. <Subject 1> appears only once.\n\n"
        "detailed_description:\n"
        f"[Shot 1] {scene}.{_dialogue(say, lang)}\n\n"
        "overall_soundscape:\n"
        f"{_soundscape(sound, voice, say)}\n\n"
        "non_diegetic_music:\n"
        f"{music.strip() if music else 'N/A'}"
    )


def image_prompt(
    scene: str,
    say: str | None = None,
    voice: str | None = None,
    sound: str | None = None,
    music: str | None = None,
    lang: str = "English",
) -> str:
    """fl2va 用。<Picture 1>（最初のフレーム）を 0 秒目として完全に参照させる。"""
    scene = scene.strip().rstrip(".")
    return (
        "integrated_multimodal_description: For the target video, at 0.00 seconds into the target "
        "video, <Picture 1> (from [Shot 1]) is fully referenced.\n\n"
        "[Shot 1] Preserve the subject, clothing, and scene from <Picture 1>, then: "
        f"{scene}.{_dialogue(say, lang).replace('<Subject 1>', 'The person')}\n\n"
        "overall_soundscape: "
        f"{_soundscape(sound, voice, say).replace('<Subject 1>', 'The person')}\n\n"
        f"non_diegetic_music: {music.strip() if music else 'N/A'}"
    )


class VideoWorkflowBuilder:
    def __init__(self, video: dict[str, Any]):
        self.models = video["models"]
        self.video = video

    def _base(
        self, graph: Graph, mode: str, steps: int | None, turbo: bool | None, lora: str | None = None
    ) -> tuple[list, list, list, list, int, str]:
        turbo = self.video.get("turbo", True) if turbo is None else turbo
        graph["1"] = {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": self.models[mode], "weight_dtype": "default"},
        }
        model_ref: list = ["1", 0]
        if turbo:
            settings = dict(self.video["turbo_settings"][mode])
            if lora:
                # 配布元の一覧では 768p 版は shift 6/3、それ以外（544p）は 12/3 で学習されている
                settings["shift_video"] = 6.0 if "768p" in lora else 12.0
                settings["shift_audio"] = 3.0
            graph["2"] = {
                "class_type": "LoraLoaderModelOnly",
                "inputs": {"model": model_ref, "lora_name": lora or settings["lora"], "strength_model": 1.0},
            }
            model_ref = ["2", 0]
            sampler = self.video["turbo_sampler"]
        else:
            settings = self.video
            sampler = self.video["sampler"]
        # 映像の shift がサンプラのシグマ列を決め、音声の shift は DiT 側で使われる。
        # スケジューラとガイダの両方にこのノードの出力を渡す（配布元の i2v ワークフローと同じ）
        graph["7"] = {
            "class_type": "MiniMaxH3SigmaShift",
            "inputs": {
                "model": model_ref,
                "shift_video": float(settings["shift_video"]),
                "shift_audio": float(settings["shift_audio"]),
            },
        }
        model_ref = ["7", 0]
        if steps is None:
            steps = int(settings["steps"])
        graph["3"] = {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": self.models["text_encoder"], "type": "minimax", "device": "default"},
        }
        graph["4"] = {"class_type": "VAELoader", "inputs": {"vae_name": self.models["video_vae"]}}
        graph["5"] = {"class_type": "VAELoader", "inputs": {"vae_name": self.models["audio_vae"]}}
        return model_ref, ["3", 0], ["4", 0], ["5", 0], steps, sampler

    def _sample_and_save(
        self, graph: Graph, model_ref: list, sampler: str, scheduler: str, steps: int, seed: int, prefix: str
    ) -> Graph:
        graph["11"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": int(seed)}}
        graph["12"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": sampler}}
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
        scheduler: str | None = None,
        lora: str | None = None,
    ) -> Graph:
        """静止画を最初のフレームにして動かす（fl2va）。first_frame は ComfyUI input/ の名前。"""
        graph: Graph = {}
        model_ref, clip_ref, vae_ref, _, steps, sampler = self._base(graph, "fl2va", steps, turbo, lora)
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
        return self._sample_and_save(
            graph, model_ref, sampler, scheduler or self.video["scheduler_i2v"], steps, seed, prefix
        )

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
        scheduler: str | None = None,
        lora: str | None = None,
        ref_image_size: str | None = None,
    ) -> Graph:
        """参照写真の人物が出る動画を作る（ref2va）。references は ComfyUI input/ の名前。"""
        if not references:
            raise ValueError("参照画像が1枚以上必要です")
        if len(references) > MAX_REFERENCE_IMAGES:
            raise ValueError(f"参照画像は {MAX_REFERENCE_IMAGES} 枚までです")
        graph: Graph = {}
        model_ref, clip_ref, vae_ref, audio_vae_ref, steps, sampler = self._base(graph, "ref2va", steps, turbo, lora)
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
                "ref_image_size": ref_image_size or self.video.get("ref_image_size", "match"),
            },
        }
        return self._sample_and_save(
            graph, model_ref, sampler, scheduler or self.video["scheduler_r2v"], steps, seed, prefix
        )
