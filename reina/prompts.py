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


def build_prompt(character: Character, scene: Scene, reference_mode: bool = False) -> str:
    """参照画像モードでは「この人物を保ったまま〜」という編集指示文にする。"""
    if reference_mode:
        subject = character.describe()
        head = "Keep the exact same person, same face, same identity as the reference image."
        body = f"Generate a new photo of her: {scene.prompt}."
        tail = ", ".join(p for p in (character.style, character.quality) if p)
        extra = f" Distinguishing features: {subject}." if subject else ""
        return f"{head}{extra} {body} {tail}"

    parts = [character.style, character.describe(), scene.prompt, character.quality]
    return ", ".join(p.strip().rstrip(",") for p in parts if p and p.strip())


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
