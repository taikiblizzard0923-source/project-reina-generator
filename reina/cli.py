"""コマンドラインエントリポイント: python -m reina <command>"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .client import ComfyClient, ComfyError
from .config import load_config
from .prompts import (
    Character,
    Scene,
    build_negative,
    build_prompt,
    combine_axes,
    load_axes,
    load_character,
    load_scenes,
    scene_overrides,
)
from .workflows import EDIT_ENCODER_CANDIDATES, WorkflowBuilder, random_seed

ROOT = Path(__file__).resolve().parent.parent


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _progress(prefix: str, started: float):
    """KSampler のステップ進捗を1行で上書き表示する。"""
    state = {"last": 0.0}

    def report(value: int, maximum: int) -> None:
        now = time.time()
        # 端末が流れないよう更新は 0.2 秒に1回まで（最後の1回は必ず出す）
        if maximum and value < maximum and now - state["last"] < 0.2:
            return
        state["last"] = now
        elapsed = now - started
        if maximum:
            done = value / maximum
            bar = "#" * int(done * 20) + "." * (20 - int(done * 20))
            eta = f" 残り {elapsed / done - elapsed:4.0f}s" if done > 0.05 else ""
            print(f"\r  {prefix} [{bar}] {value:>3}/{maximum}  {elapsed:5.1f}s{eta}",
                  end="", file=sys.stderr, flush=True)
        else:
            print(f"\r  {prefix} 実行中  {elapsed:5.1f}s", end="", file=sys.stderr, flush=True)

    return report


def _make_client(cfg, args: argparse.Namespace) -> ComfyClient:
    url = getattr(args, "server", None) or cfg.server_url
    return ComfyClient(
        url,
        timeout=getattr(args, "timeout", 900),
        auth=cfg.auth,
        verify=cfg.verify_tls,
    )


def _resolve_character(args: argparse.Namespace) -> Character:
    if args.character:
        return load_character(args.character)
    for candidate in (ROOT / "presets/character.yaml", ROOT / "presets/character.example.yaml"):
        if candidate.exists():
            return load_character(candidate)
    return Character()


def _pick_encoder(client: ComfyClient, override: str | None) -> str:
    if override:
        return override
    for candidate in EDIT_ENCODER_CANDIDATES:
        try:
            if client.has_node(candidate):
                return candidate
        except Exception:  # noqa: BLE001 - 疎通失敗時は既定値にフォールバック
            break
    return EDIT_ENCODER_CANDIDATES[0]


# Qwen-Image 2.1 は ComfyUI v0.37.0 で正式サポートされた
MIN_COMFY_VERSION = (0, 37, 0)


def _version_tuple(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in str(text).lstrip("v").split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    # "0.37" が (0,37) となって (0,37,0) より小さく扱われないよう桁を揃える
    return tuple((parts + [0, 0, 0])[:3])


def _out_dir(args: argparse.Namespace) -> Path:
    base = Path(args.out) if args.out else ROOT / "output"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = base / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save(out_dir: Path, stem: str, images: list[tuple[str, bytes]], meta: dict[str, Any]) -> list[Path]:
    saved: list[Path] = []
    for index, (_, data) in enumerate(images):
        suffix = f"_{index}" if len(images) > 1 else ""
        path = out_dir / f"{stem}{suffix}.png"
        path.write_bytes(data)
        saved.append(path)
    if saved:
        (out_dir / f"{stem}.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return saved


def _cli_overrides(args: argparse.Namespace) -> dict[str, Any]:
    keys = ("width", "height", "steps", "cfg", "denoise", "batch_size")
    return {k: getattr(args, k, None) for k in keys if getattr(args, k, None) is not None}


# -- サブコマンド -----------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    _log(f"config: {cfg.path}")
    client = _make_client(cfg, args)
    _log(f"server: {client.base_url}")

    try:
        stats = client.ping()
    except ComfyError as exc:
        _log(f"[NG] {exc}")
        _log("     ローカル: ComfyUI を起動 (python main.py --listen 127.0.0.1 --port 8188)")
        _log("     RunPod  : Pod が Running か、8188 が HTTP ポートとして公開されているか確認")
        return 1

    ok = True
    version = (stats.get("system") or {}).get("comfyui_version")
    if version:
        if _version_tuple(version) >= MIN_COMFY_VERSION:
            _log(f"[OK] ComfyUI version {version}")
        else:
            ok = False
            _log(f"[NG] ComfyUI version {version}")
            _log(
                "     Qwen-Image 2.1 は v"
                + ".".join(map(str, MIN_COMFY_VERSION))
                + " 以降が必要です（git -C ~/ComfyUI pull して再起動）"
            )

    devices = stats.get("devices") or []
    if devices:
        dev = devices[0]
        total = dev.get("vram_total", 0) / 1e9
        free = dev.get("vram_free", 0) / 1e9
        _log(f"[OK] ComfyUI {client.base_url} / {dev.get('name')} VRAM {free:.1f}/{total:.1f} GB free")
    else:
        _log(f"[OK] ComfyUI {client.base_url}")

    unet_name = str(cfg.models["unet_gguf"])
    unet_node = "UnetLoaderGGUF" if unet_name.lower().endswith(".gguf") else "UNETLoader"

    for node in (unet_node, "CLIPLoader", "VAELoader", "KSampler", "ModelSamplingAuraFlow"):
        present = client.has_node(node)
        ok &= present
        _log(f"{'[OK]' if present else '[NG]'} node {node}")
    if cfg.models.get("text_encoder_is_gguf") and not client.has_node("CLIPLoaderGGUF"):
        ok = False
        _log("[NG] node CLIPLoaderGGUF (ComfyUI-GGUF が必要)")

    encoder = next((c for c in EDIT_ENCODER_CANDIDATES if client.has_node(c)), None)
    if encoder:
        _log(f"[OK] 参照画像エンコーダ: {encoder}")
    else:
        _log("[NG] 参照画像エンコーダが見つかりません (TextEncodeQwenImageEditPlus 等)")
        _log("     → ComfyUI を最新に更新するか、t2i + LoRA 運用に切り替えてください")

    clip_class = "CLIPLoaderGGUF" if cfg.models.get("text_encoder_is_gguf") else "CLIPLoader"
    checks = (
        # ラベル, 期待値, (ノード, 入力名), フォールバックで見る models/ のフォルダ
        ("unet", unet_name, (unet_node, "unet_name"),
         ("unet_gguf", "unet", "diffusion_models")),
        ("text_encoder", cfg.models["text_encoder"], (clip_class, "clip_name"),
         ("text_encoders", "clip", "clip_gguf")),
        ("vae", cfg.models["vae"], ("VAELoader", "vae_name"), ("vae",)),
    )
    for label, expected, (node, input_name), folders in checks:
        # ノードの選択肢が最も確実。取れなければ models/ 一覧にフォールバック
        found = client.node_options(node, input_name)
        source = f"{node}.{input_name}"
        if not found:
            found = [f for folder in folders for f in client.model_list(folder)]
            source = "models/"
        if expected in found:
            _log(f"[OK] {label}: {expected}")
        else:
            ok = False
            _log(f"[NG] {label}: '{expected}' が {source} の選択肢にありません")
            if found:
                _log(f"     候補: {', '.join(sorted(set(found))[:12])}")
            else:
                _log(f"     {source} から候補を取得できませんでした")

    return 0 if ok else 1


def _run_jobs(
    args: argparse.Namespace,
    character: Character,
    scenes: list[Scene],
    reference_paths: list[str],
) -> int:
    cfg = load_config(args.config)
    client = _make_client(cfg, args)
    builder = WorkflowBuilder(cfg.models, cfg.defaults, cfg.loras)
    reference_mode = bool(reference_paths)

    uploaded: list[str] = []
    if reference_mode and not args.dry_run:
        for path in reference_paths:
            name = client.upload_image(path)
            uploaded.append(name)
            _log(f"参照画像をアップロード: {path} -> {name}")
    elif reference_mode:
        uploaded = [Path(p).name for p in reference_paths]

    encoder = _pick_encoder(client, args.encoder) if reference_mode and not args.dry_run else (
        args.encoder or EDIT_ENCODER_CANDIDATES[0]
    )

    out_dir = _out_dir(args)
    negative = build_negative(character, args.negative or "")
    overrides = _cli_overrides(args)
    total = len(scenes) * args.repeat
    done = 0
    failures = 0
    run_started = time.time()

    for scene in scenes:
        for take in range(args.repeat):
            done += 1
            params = dict(overrides)
            params.update(scene_overrides(scene))
            if args.seed is not None:
                params["seed"] = args.seed + take
            params.setdefault("seed", random_seed())
            params["prefix"] = f"{character.name}/{scene.id}"

            prompt = build_prompt(character, scene, reference_mode=reference_mode)
            if reference_mode:
                graph = builder.reference_to_image(
                    prompt, uploaded, negative=negative, encoder_class=encoder, **params
                )
            else:
                graph = builder.text_to_image(prompt, negative=negative, **params)

            stem = f"{scene.id}_{params['seed']}" if args.repeat == 1 else f"{scene.id}_t{take}_{params['seed']}"

            if args.dry_run:
                print(json.dumps({"stem": stem, "prompt": prompt, "workflow": graph}, ensure_ascii=False, indent=2))
                continue

            # ComfyUI 側でノードに必須入力が増えていても通るように補完する
            added = client.fill_missing_inputs(graph)
            if added and done == 1:
                _log(f"  既定値で補完: {', '.join(added)}")

            _log(f"[{done}/{total}] {scene.id} seed={params['seed']}")
            started = time.time()
            try:
                images = client.run(graph, on_progress=_progress(scene.id, started))
            except ComfyError as exc:
                failures += 1
                print("", file=sys.stderr)
                _log(f"  [NG] {time.time() - started:.1f}s で失敗: {exc}")
                continue
            print("", file=sys.stderr)  # 進捗行を閉じる
            _log(f"  {time.time() - started:.1f}s で完了")

            # defaults とマージした実効値を残す（.json だけで再現できるように）
            effective = {**cfg.defaults, **params}
            effective.pop("prefix", None)
            meta = {
                "scene": scene.id,
                "prompt": prompt,
                "negative": negative,
                "params": effective,
                "reference_images": uploaded,
                "models": cfg.models,
                "loras": cfg.loras,
            }
            saved = _save(out_dir, stem, images, meta)
            for path in saved:
                _log(f"  -> {path}")

    if not args.dry_run:
        wall = time.time() - run_started
        per = f" / 1枚あたり {wall / total:.1f}s" if total else ""
        _log(f"完了: {out_dir} (失敗 {failures}/{total}) 合計 {wall:.1f}s{per}")
    return 1 if failures else 0


def cmd_generate(args: argparse.Namespace) -> int:
    character = _resolve_character(args)
    scenes = [Scene(id=args.name, prompt=args.prompt)]
    return _run_jobs(args, character, scenes, args.reference or [])


def cmd_batch(args: argparse.Namespace) -> int:
    character = _resolve_character(args)
    scenes_path = args.scenes or (ROOT / "presets/scenes.yaml")
    scenes = load_scenes(scenes_path)
    if args.only:
        wanted = set(args.only)
        scenes = [s for s in scenes if s.id in wanted]
        if not scenes:
            _log(f"該当シーンなし: {', '.join(sorted(wanted))}")
            return 1
    if args.limit:
        scenes = scenes[: args.limit]
    return _run_jobs(args, character, scenes, args.reference or [])


def cmd_mix(args: argparse.Namespace) -> int:
    character = _resolve_character(args)
    axes_path = args.axes or (ROOT / "presets/axes.yaml")
    axes = load_axes(axes_path)
    scenes = list(combine_axes(axes, limit=args.limit, seed=args.mix_seed))
    if not scenes:
        _log(f"軸が空です: {axes_path}")
        return 1
    return _run_jobs(args, character, scenes, args.reference or [])


# -- パーサ -----------------------------------------------------------------


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="config.yaml のパス")
    parser.add_argument(
        "--server",
        help="ComfyUI の URL。例: https://<POD_ID>-8188.proxy.runpod.net（環境変数 REINA_COMFY_URL でも可）",
    )
    parser.add_argument("--character", help="キャラクター定義 YAML")
    parser.add_argument("-r", "--reference", action="append", help="参照画像（最大3枚、複数指定可）")
    parser.add_argument("-o", "--out", help="出力ディレクトリ（既定: output/）")
    parser.add_argument("--negative", help="ネガティブプロンプトに追記")
    parser.add_argument("--seed", type=int, help="固定シード（--repeat で +1 ずつ）")
    parser.add_argument("--repeat", type=int, default=1, help="1シーンあたりの生成枚数")
    parser.add_argument("--width", type=int)
    parser.add_argument("--height", type=int)
    parser.add_argument("--steps", type=int)
    parser.add_argument("--cfg", type=float)
    parser.add_argument("--denoise", type=float, help="参照画像モードで下げると参照に忠実")
    parser.add_argument("--batch-size", type=int, dest="batch_size")
    parser.add_argument("--encoder", help="参照画像エンコーダのノード名を明示指定")
    parser.add_argument("--timeout", type=int, default=900, help="1枚あたりの待機上限(秒)")
    parser.add_argument("--dry-run", action="store_true", help="送信せずワークフローJSONを表示")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reina",
        description="Qwen-Image 2.1 (GGUF) + ComfyUI で参照画像ベースの人物画像を生成する",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="ComfyUI との疎通・ノード・モデルファイルを確認")
    doctor.add_argument("--config")
    doctor.add_argument("--server", help="ComfyUI の URL（RunPod の Proxy URL など）")
    doctor.add_argument("--timeout", type=int, default=900)
    doctor.set_defaults(func=cmd_doctor)

    gen = sub.add_parser("generate", help="プロンプト1件を生成")
    gen.add_argument("prompt", help="シーン説明（服装・場所・光・構図など）")
    gen.add_argument("--name", default="single", help="出力ファイル名の識別子")
    _add_common(gen)
    gen.set_defaults(func=cmd_generate)

    batch = sub.add_parser("batch", help="presets/scenes.yaml を一括生成")
    batch.add_argument("--scenes", help="シーン定義 YAML")
    batch.add_argument("--only", nargs="+", help="指定した scene id のみ")
    batch.add_argument("--limit", type=int, help="先頭 N シーンのみ")
    _add_common(batch)
    batch.set_defaults(func=cmd_batch)

    mix = sub.add_parser("mix", help="presets/axes.yaml の組み合わせで大量バリエーション生成")
    mix.add_argument("--axes", help="軸定義 YAML")
    mix.add_argument("--limit", type=int, default=12, help="生成する組み合わせ数")
    mix.add_argument("--mix-seed", type=int, help="組み合わせシャッフルのシード")
    _add_common(mix)
    mix.set_defaults(func=cmd_mix)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "reference", None) and len(args.reference) > 3:
        _log("参照画像は最大3枚です")
        return 1
    try:
        return args.func(args)
    except ComfyError as exc:
        _log(f"エラー: {exc}")
        return 1
    except KeyboardInterrupt:
        _log("中断しました")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
