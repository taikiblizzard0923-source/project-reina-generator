"""キャラクター定義とシーン定義からプロンプト文字列を組み立てる。"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import yaml


@dataclass
class Character:
    name: str = "subject"
    identity: str = ""
    details: list[str] = field(default_factory=list)
    style: str = "photorealistic portrait photograph, natural skin texture, sharp focus"
    quality: str = "high detail, realistic lighting, 85mm lens, shallow depth of field"
    negative: str = ""

    def describe(self) -> str:
        parts = [self.identity, *self.details]
        return ", ".join(p.strip().rstrip(",") for p in parts if p and p.strip())


@dataclass
class Scene:
    id: str
    prompt: str
    width: int | None = None
    height: int | None = None
    steps: int | None = None
    cfg: float | None = None
    seed: int | None = None


def load_character(path: str | Path) -> Character:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return Character(
        name=raw.get("name", "subject"),
        identity=raw.get("identity", ""),
        details=list(raw.get("details") or []),
        style=raw.get("style", Character.style),
        quality=raw.get("quality", Character.quality),
        negative=raw.get("negative", ""),
    )


def load_scenes(path: str | Path) -> list[Scene]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    scenes = raw.get("scenes") or []
    out: list[Scene] = []
    for index, item in enumerate(scenes):
        if isinstance(item, str):
            out.append(Scene(id=f"scene{index:02d}", prompt=item))
        else:
            out.append(
                Scene(
                    id=str(item.get("id", f"scene{index:02d}")),
                    prompt=item["prompt"],
                    width=item.get("width"),
                    height=item.get("height"),
                    steps=item.get("steps"),
                    cfg=item.get("cfg"),
                    seed=item.get("seed"),
                )
            )
    return out


def load_axes(path: str | Path) -> dict[str, list[str]]:
    """outfit / location / lighting などの軸を読む（組み合わせ生成用）。"""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    axes = raw.get("axes") or {}
    return {key: list(values) for key, values in axes.items() if values}


def combine_axes(
    axes: dict[str, list[str]], limit: int | None = None, shuffle: bool = True, seed: int | None = None
) -> Iterator[Scene]:
    """軸の直積からシーンを作る。limit で打ち切り。"""
    if not axes:
        return iter(())
    keys = list(axes)
    combos = list(itertools.product(*(axes[k] for k in keys)))
    if shuffle:
        random.Random(seed).shuffle(combos)
    if limit:
        combos = combos[:limit]

    def _gen() -> Iterator[Scene]:
        for index, combo in enumerate(combos):
            yield Scene(id=f"mix{index:03d}", prompt=", ".join(combo))

    return _gen()


def build_prompt(
    character: Character,
    scene: Scene,
    reference_mode: bool = False,
    keep_pose: bool = False,
    identity_count: int = 0,
    scene_labels: list[str] | None = None,
) -> str:
    """参照画像モードの指示文を組み立てる。

    参照画像エンコーダは画像編集用なので、素直に書くと顔だけでなく
    ポーズ・髪型・構図まで参照画像のまま引き継ぎ、
    服と背景だけが変わった絵になる。keep_pose=False ではそれを明示的に禁止する。

    identity_count 枚目までが人物、その後が「写り込ませたいもの」
    （部屋・ペットなど）。後者は逆に見た目を引き継がせたいので、
    「コピーするな」の対象から外す必要がある。
    """
    if not reference_mode:
        parts = [character.style, character.describe(), scene.prompt, character.quality]
        return ", ".join(p.strip().rstrip(",") for p in parts if p and p.strip())

    scene_labels = scene_labels or []
    identity_count = max(identity_count, 0)
    sentences: list[str] = []

    # どの画像が何なのか
    if identity_count > 1:
        person_ref = f"Images 1 to {identity_count}"
        sentences.append(f"{person_ref} show the same person from different angles.")
    elif identity_count == 1:
        person_ref = "Image 1"
        sentences.append("Image 1 shows the person.")
    else:
        person_ref = "The reference image"
    for offset, label in enumerate(scene_labels, start=max(identity_count, 1) + 1):
        sentences.append(f"Image {offset} shows {label}.")

    if keep_pose:
        sentences.append(
            f"Keep the person exactly as in {person_ref.lower()}: "
            "same face, same identity, same pose and same framing."
        )
    else:
        sentences.append(
            f"Use {person_ref.lower()} only for the identity of the person: "
            "the same facial features, so that the person is recognisable "
            "as the same individual."
        )
        sentences.append(
            f"Do not copy anything else from {person_ref.lower()} — "
            "not the pose, the head angle, the direction the person is facing, "
            "the gaze, the camera angle, the framing, the crop, the expression, "
            "the hairstyle, the hair length, the makeup, the clothing "
            "or the background."
        )

    subject = character.describe()
    if subject:
        sentences.append(f"Distinguishing features: {subject}.")

    if scene_labels:
        listed = (
            " and ".join(scene_labels)
            if len(scene_labels) < 3
            else ", ".join(scene_labels[:-1]) + " and " + scene_labels[-1]
        )
        sentences.append(
            f"Include {listed} in the photograph, "
            "matching how they look in their own reference images."
        )

    if keep_pose:
        sentences.append(f"Change the scene to: {scene.prompt}.")
    else:
        sentences.append(
            "Take a completely new photograph of this person, with a different pose, "
            f"a different head angle and a different hairstyle: {scene.prompt}."
        )

    tail = ", ".join(p for p in (character.style, character.quality) if p)
    return " ".join(sentences) + (f" {tail}" if tail else "")


DEFAULT_NEGATIVE = (
    "lowres, blurry, out of focus, jpeg artifacts, worst quality, "
    "deformed hands, extra fingers, extra limbs, bad anatomy, "
    "watermark, text, logo, signature, plastic skin, oversaturated, cgi, 3d render, doll"
)


def build_negative(character: Character, extra: str = "") -> str:
    parts = [character.negative or DEFAULT_NEGATIVE]
    if extra:
        parts.append(extra)
    return ", ".join(p for p in parts if p)


def scene_overrides(scene: Scene) -> dict[str, Any]:
    return {
        k: v
        for k, v in {
            "width": scene.width,
            "height": scene.height,
            "steps": scene.steps,
            "cfg": scene.cfg,
            "seed": scene.seed,
        }.items()
        if v is not None
    }
