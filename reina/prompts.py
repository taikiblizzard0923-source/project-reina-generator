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
    # 「高精細で綺麗」に寄せる語は AI 感を強めるので、
    # あえて素人写真・フィルム・粗さの側に振っている
    style: str = (
        # 構図が整いすぎているのも AI 感の一因なので、
        # 「たまたま撮れた1枚」の側に振っている
        "candid unposed snapshot, caught mid-movement, casually framed, "
        "slightly off-center composition, slightly tilted horizon, "
        "shot on 35mm film, Kodak Portra 400, natural film grain, "
        "true-to-life muted colors, imperfect available lighting, not retouched"
    )
    quality: str = (
        "visible skin pores and fine skin texture, uneven skin tone, "
        "small blemishes and freckles, flyaway hairs, natural catchlights, "
        "slight lens softness, realistic depth of field"
    )
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


def _join(items: list[str]) -> str:
    if len(items) < 3:
        return " and ".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _capitalize(text: str) -> str:
    return text[:1].upper() + text[1:]


def _image_ref(index: int, total: int, picture_labels: bool) -> str:
    """プロンプト中での画像の呼び方。エンコーダが各画像の直前に差し込むラベルに合わせる。

    picture_labels=True（TextEncodeQwenImageEditPlus）: 枚数によらず "Picture N"。
    False（TextEncodeQwenImage21）: Qwen-Image 2.1 の規約で、2枚以上なら <imageN>、
    1枚だけならタグを使わず "the image"。どちらも範囲指定（"images 1 to 2"）は不可。
    """
    if picture_labels:
        return f"Picture {index}"
    return f"<image{index}>" if total > 1 else "the image"


def build_edit_prompt(instruction: str, identity_count: int = 0, picture_labels: bool = False) -> str:
    """生成済みの画像（1枚目）を指示どおりに直す指示文。

    2枚目以降は顔の参照。編集を繰り返すと顔が少しずつ別人に寄っていくので、
    参照写真を添えたときはそちらの顔に合わせるよう明示する。
    """
    total = 1 + max(identity_count, 0)
    target = _image_ref(1, total, picture_labels)
    sentences: list[str] = []
    if identity_count > 0:
        faces = _join([_image_ref(i, total, picture_labels) for i in range(2, total + 1)])
        verb = "shows" if identity_count == 1 else "show"
        sentences.append(
            f"{faces} {verb} the same person as {target}. "
            f"Keep the person's face exactly as in {faces}."
        )
    sentences.append(f"Edit {target}: {instruction.strip().rstrip('.')}.")
    sentences.append(
        f"Keep everything else in {target} unchanged: the person, the pose, "
        "the framing, the lighting and the background."
    )
    return " ".join(sentences)


def build_prompt(
    character: Character,
    scene: Scene,
    reference_mode: bool = False,
    keep_pose: bool = False,
    identity_count: int = 0,
    scene_labels: list[str] | None = None,
    picture_labels: bool = False,
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
    total = identity_count + len(scene_labels)
    sentences: list[str] = []

    def ref(index: int) -> str:
        return _image_ref(index, total, picture_labels)

    identity_refs = [ref(i) for i in range(1, identity_count + 1)]
    if identity_count > 1:
        person_ref = _join(identity_refs)
        sentences.append(f"{person_ref} are photos of the same person from different angles.")
    elif identity_count == 1:
        person_ref = identity_refs[0]
        sentences.append(f"{_capitalize(person_ref)} shows the person.")
    else:
        person_ref = "the reference image"
    for offset, label in enumerate(scene_labels, start=identity_count + 1):
        sentences.append(f"{_capitalize(ref(offset))} shows {label}.")

    if keep_pose:
        sentences.append(
            f"Keep the person exactly as in {person_ref}: "
            "same face, same identity, same pose and same framing."
        )
    else:
        sentences.append(
            f"Use {person_ref} only for the identity of the person: "
            "the same facial features, so that the person is recognisable "
            "as the same individual."
        )
        sentences.append(
            f"Do not copy anything else from {person_ref} — "
            "not the pose, the head angle, the direction the person is facing, "
            "the gaze, the camera angle, the framing, the crop, the expression, "
            "the hairstyle, the hair length, the makeup, the clothing "
            "or the background."
        )

    subject = character.describe()
    if subject:
        sentences.append(f"Distinguishing features: {subject}.")

    if scene_labels:
        sentences.append(
            f"Include {_join(scene_labels)} in the photograph, "
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
    # 構図が整いすぎるのを抑える
    "posed studio portrait, centered symmetrical composition, "
    "professional headshot, stock photo, "
    # AI 生成っぽさの原因になる語を潰す
    "airbrushed, smooth plastic skin, waxy skin, poreless skin, "
    "beauty filter, heavy retouching, glamour shot, magazine cover, "
    "perfectly symmetrical face, flawless complexion, "
    "oversaturated, HDR, overprocessed, excessive contrast, glowing skin, "
    "cgi, 3d render, digital art, illustration, anime, doll, mannequin, "
    # 画質の破綻
    "lowres, blurry, out of focus, jpeg artifacts, worst quality, "
    "watermark, text, logo, signature"
)

# 見た目の好みと違って人体の破綻対策は常に要るので、character.yaml で negative を
# 書き換えても外れないよう別枠で必ず足す
ANATOMY_NEGATIVE = (
    "bad anatomy, deformed body, extra limbs, extra legs, extra arms, "
    "three legs, missing limbs, fused legs, fused fingers, extra fingers, "
    "deformed hands, malformed feet, disconnected limbs"
)


def build_negative(character: Character, extra: str = "") -> str:
    parts = [character.negative or DEFAULT_NEGATIVE, ANATOMY_NEGATIVE]
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
