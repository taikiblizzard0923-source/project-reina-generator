"""ComfyUI API の最小モック。

GPU もモデルも無い環境で、生成まわりの経路を検証するために使う。
WebSocket は実装していないので、クライアントの /history ポーリング経路も同時に確かめられる。

    python tests/mock_comfyui.py     # 127.0.0.1:8188 で待ち受ける

実機と同じ振る舞いを意図的に再現している点:
  - GGUF は /models/unet の一覧に出てこない（拡張子が対象外のため）
  - ノードの選択肢は /object_info から返る
  - 必須入力が欠けたワークフローは 400 で弾く
"""
import base64, json, re
from http.server import BaseHTTPRequestHandler, HTTPServer

PNG = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
# ComfyUI 更新で必須入力が増えた状況を再現する
ENCODER_IMAGES = 5
REQUIRED = {
    "ImageScaleToTotalPixels": {
        "image": [["IMAGE"], {}],
        "upscale_method": [["nearest-exact", "lanczos"], {}],
        "megapixels": [["FLOAT"], {"default": 1.0}],
        "resolution_steps": [["INT"], {"default": 64}],
    },
    "KSampler": {
        "sampler_name": [["euler", "er_sde", "dpmpp_2m"], {}],
        "scheduler": [["simple", "beta", "karras"], {}],
    },
}
OPTIONS = {
    "UnetLoaderGGUF": ("unet_name", ["qwen-image-2.1-Q4_K_M.gguf"]),
    "CLIPLoader": ("clip_name", ["qwen3vl_8b_int8_convrot.safetensors"]),
    "VAELoader": ("vae_name", ["qwen_image_2.1_vae_bf16.safetensors"]),
}
NODES = set(OPTIONS) | set(REQUIRED) | {
    "KSampler", "ModelSamplingAuraFlow", "TextEncodeQwenImageEditPlus",
    "LoadImage", "VAEEncode", "VAEDecode", "SaveImage", "EmptySD3LatentImage",
    "CLIPTextEncode", "UNETLoader",
}
# 実機同様、GGUF は標準の models/unet 一覧には現れない
MODELS = {"unet": [], "text_encoders": ["qwen3vl_8b_int8_convrot.safetensors"],
          "vae": ["qwen_image_2.1_vae_bf16.safetensors"]}
STATE = {"prompts": {}}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _j(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/system_stats":
            import os
            return self._j({
                "system": {"comfyui_version": os.environ.get("MOCK_VER", "0.37.0")},
                "devices": [{"name": "MockGPU", "vram_total": 24e9, "vram_free": 22e9}],
            })
        if m := re.fullmatch(r"/object_info/(.+)", path):
            name = m.group(1)
            if name not in NODES:
                return self._j({}, 404)
            spec = {}
            if name in OPTIONS:
                key, values = OPTIONS[name]
                spec = {"required": {key: [values, {}]}}
            if name in REQUIRED:
                spec = {"required": REQUIRED[name]}
            if name == "TextEncodeQwenImageEditPlus":
                spec = {
                    "required": {"clip": [["CLIP"], {}], "prompt": [["STRING"], {}]},
                    "optional": {f"image{i}": [["IMAGE"], {}] for i in range(1, ENCODER_IMAGES + 1)},
                }
            return self._j({name: {"input": spec}})
        if m := re.fullmatch(r"/models/(.+)", path):
            return self._j(MODELS.get(m.group(1), []))
        if m := re.fullmatch(r"/history/(.+)", path):
            pid = m.group(1)
            rec = STATE["prompts"].get(pid)
            if not rec: return self._j({})
            rec["polls"] += 1
            if rec["polls"] < 2:  # 1回目は未完了 → ポーリング継続を検証
                return self._j({pid: {"status": {"completed": False, "status_str": "running"}}})
            return self._j({pid: {"status": {"completed": True},
                                  "outputs": {"22": {"images": [{"filename": "mock_00001_.png",
                                                                 "subfolder": "", "type": "output"}]}}}})
        if path == "/view":
            self.send_response(200); self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(PNG))); self.end_headers(); self.wfile.write(PNG); return
        self._j({}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); raw = self.rfile.read(n)
        if self.path == "/upload/image":
            return self._j({"name": "me.jpg", "subfolder": "reina", "type": "input"})
        if self.path == "/prompt":
            wf = json.loads(raw)["prompt"]
            assert "20" in wf and wf["20"]["class_type"] == "KSampler", "KSampler が無い"
            for node_id, node in wf.items():
                for key in REQUIRED.get(node["class_type"], {}):
                    if key not in node.get("inputs", {}):
                        return self._j({"error": {"type": "prompt_outputs_failed_validation"},
                                        "node_errors": {node_id: {"errors": [
                                            {"type": "required_input_missing", "details": key}]}}}, 400)
            pid = f"pid-{len(STATE['prompts'])}"
            STATE["prompts"][pid] = {"polls": 0, "workflow": wf}
            return self._j({"prompt_id": pid})
        self._j({}, 404)

HTTPServer(("127.0.0.1", 8188), H).serve_forever()
