# text2scene

把一份場景文字描述變成 **Unreal Engine 5 或 Unity 6** 裡可以玩的關卡，不需要參考圖。

- **資產在 Blender 做，場景在引擎組。** Blender 只在背景批次處理資產（清理、烘焙、LOD、碰撞、模組件）；格局、擺位、燈光、後處理全部在引擎裡生成。
- **引擎可以換。** 所有資產與關卡先輸出成引擎中立的 `export/`（FBX + PNG + JSON），再由各引擎的匯入器建出關卡；同一份資料可以同時匯入 Unreal 與 Unity。
- **針對「塑膠感」與效能設計。** 粗糙度擾動、髒污與邊緣磨損、Albedo 範圍限制、倒角、貼圖密度統一、世界座標大尺度變化、貼花；LOD、Nanite、instancing、碰撞簡化、效能預算報告。

![同一份 level.json 在 Unreal（上）與 Unity（下）的審查截圖](preview.png)

## 流程

```
場景文字描述
  │  agent 讀文字 → 空間配置（格線）、物件 / 材質 / 貼花清單 → assets.json + layout.json
  ▼
資產
  ├─ 道具：FLUX.2 klein 4B 生成白底圖（逐張審核、改 prompt）→ RMBG 去背 → TRELLIS.2 → GLB
  │        → Blender 背景批次：刪內殼、縮放與原點、烘焙 AO / 曲率 / 法線、依「新舊程度」做髒污與磨損、
  │          貼圖密度、LOD、碰撞 → FBX + PNG，並產生報告（只需要看被標記的）
  ├─ 平面影像（海報、螢幕）：FLUX 正面圖 → 帶框薄板
  ├─ 貼花：FLUX 白底俯拍 → 去背 + 法線
  ├─ 表面材質：Poly Haven / ambientCG（CC0 掃描）或程序化平塗
  └─ 模組件：Blender 依格局需要產生牆、門、窗、柱、地板、天花（倒角、加權法線、公尺 UV、碰撞）
  ▼
30_layout.py：rooms → 全域牆圖（共用牆只建一次）、道具擺位、散佈、貼花、燈光陣列、探針、鏡頭、效能預算
  ▼
export/（引擎中立） ──► Unreal：主材質 + 材質實例、Nanite 網格、關卡、Lumen、天空、霧、後處理、截圖
                    └─► Unity：URP / HDRP / Built-in 材質、LODGroup prefab、場景、Volume、探針、截圖
```

逐步操作說明在 [AGENT.md](AGENT.md)（寫給執行管線的 AI agent），美術與效能的數字標準在 [docs/STANDARDS.md](docs/STANDARDS.md)。

## 需求

| 用途 | 需要 |
|---|---|
| 2D 圖與 3D 模型 | Windows 11 + WSL2 Ubuntu、NVIDIA 12 GB VRAM 以上（RTX 3060 測試）、[TRELLIS.2](https://github.com/microsoft/TRELLIS.2)（`~/TRELLIS.2`，conda env `trellis2`）、[ComfyUI](https://github.com/comfyanonymous/ComfyUI)（`~/ComfyUI`，conda env `comfyui`）與 [FLUX.2 klein](https://docs.comfy.org/tutorials/flux/flux-2-klein) 4B 模型檔 |
| 資產處理 | Blender 5.x（背景執行，不需開介面）；Windows Python 3 + numpy + Pillow |
| 引擎（擇一或都要） | Unreal Engine 5.x（Python Editor Script Plugin、Editor Scripting Utilities，皆隨引擎附帶），或 Unity 6（URP / HDRP / Built-in 皆可；URP 貼花需要 Renderer 上的 Decal 功能） |
| 選用 | MCP for Blender：在即時 Blender 裡調整道具烘焙參數 |

把 `examples/t2s.config.json` 複製到 repo 根目錄並填入 Blender、引擎執行檔與引擎專案的路徑。

## 使用方式

建議讓 Claude Code 之類的 agent 依照 `AGENT.md` 執行（列物件、寫 prompt、審圖、擺位都需要判斷）：

> 依照 AGENT.md，只看 `<場景描述.md>` 的文字，把「區域 A」做成 Unreal 關卡。

也可以手動逐步執行，每一步都是一個指令（`-Project` 指向放 `assets.json` / `layout.json` 的資料夾）：

```powershell
.\pipeline\run.ps1 images    -Project D:\MyScene   # FLUX 候選圖 → 看 sheet.png、寫 work\selection.json
.\pipeline\run.ps1 models    -Project D:\MyScene   # 去背 + TRELLIS
.\pipeline\run.ps1 build     -Project D:\MyScene -Shots
#   = 共用貼圖、材質、貼花、layout、模組件、道具烘焙、layout（實際面數）、匯入引擎並截圖
```

單獨的步驟：`materials`、`decals`、`layout`、`kit`、`props`（`-Retexture` 只重做髒污與匯出、`-Ids a,b` 只做部分）、`engine`。
目標引擎由 `assets.json` 的 `"target": {"engine": "unreal" | "unity"}` 決定，改這一個欄位再跑 `engine` 就換引擎。

範例專案：`examples/assets.json` 與 `examples/layout.json`（戶外廣場 → 大廳 → 走廊 → 辦公室）。

## 輸出

| 位置 | 內容 |
|---|---|
| `export/props/SM_<id>/` | `SM_<id>.fbx`（LOD0 + `UCX_` 碰撞）、`SM_<id>_LOD<n>.fbx`、`T_SM_<id>_{BC,N,ORM,MASK}.png`、`asset.json`、`preview.png` |
| `export/props/bake_report.md` | 每個道具的面數、LOD、貼圖密度、烘焙異常與狀態（`ok` / `check` / `review` / `fail`） |
| `export/kit/` | 模組件、`preview_<set>.png`、座標軸校正件 `SM_T2S_Axes` |
| `export/materials/`、`export/decals/`、`export/shared/` | 表面材質、貼花、macro variation 與細節法線 |
| `export/level.json` | 引擎中立的關卡：實例、燈、貼花、探針範圍、鏡頭、PlayerStart、光照與後處理設定 |
| `export/budget.json` | 每個空間的三角面、實例、貼花、半透明、重疊陰影燈；全場景貼圖記憶體與材質數 |
| `export/shots/<engine>/` | 每台審查鏡頭的引擎截圖 |
| Unreal `/Game/T2S/` | `Master/`、`Materials/`、`Props/`、`Kit/`、`Decals/`、`Maps/<name>` |
| Unity `Assets/T2S/` | `Materials/`、`Prefabs/`、`Scenes/<name>.unity` |

重跑是安全的：未變更的資產會跳過，關卡中由管線產生的物件會重建，手動加入的不受影響。

## 效能參考（RTX 3060 12 GB）

| 階段 | 耗時 |
|---|---|
| FLUX.2 klein 4B，1 MP、4 步 | 冷啟動首張 2–4 分鐘，之後約 15 秒/張 |
| TRELLIS.2（512 管線） | 載入約 5 分鐘，每個物件 1–4 分鐘 |
| Blender 道具烘焙（GPU） | 每個 5–15 秒；`-Retexture` 約 2 秒 |
| 模組件 | 一整套 < 30 秒 |
| Unreal 匯入 + 關卡 + 截圖 | 新專案第一次編譯著色器 5–15 分鐘，之後 1–3 分鐘 |
| Unity 匯入 + 場景 + 截圖 | 第一次 3–5 分鐘，之後約 1 分鐘 |

## 已知限制

- **植被與地形不在管線內**：草、灌木、樹林、地形起伏請用 Unreal Foliage / PCG / Landscape 或 Unity Terrain；總結會列出需要補的位置。
- **模組化格局**：空間對齊格線（預設 2 m），牆只有直角；斜牆、曲面、樓梯要另外做成道具或在引擎裡補。門洞只有門框，門扇與開關門互動需在引擎裡加。
- **TRELLIS 的限制照舊**：透明物件會變成白色實體、文字會變亂碼、姿態無法事後調整；做法見 AGENT.md 步驟 0。
- **去光影只能減輕**：AI 生成的貼圖常烘進光影，`delight` 只能壓平大尺度明暗。
- **貼圖重複**：表面材質的 `tile_m` 要能整除模組尺寸，否則每個模組會出現接縫；macro variation 只在 Unreal 主材質裡（Unity 端靠貼花與散佈打破重複）。
- **Unity 的燈光強度**：URP / Built-in 沒有物理單位，流明是近似換算；HDRP 使用物理單位。Unity 端的 GI 需要烘焙（`-t2sBake` 或手動）。
- **Unreal 截圖需要編輯器視窗**：匯入器會開啟編輯器、跑完自動關閉，期間不要關掉它。
- **模型是想像的**：沒有參考圖，風格一致性靠 prompt 與審核維持；同一份文字跑兩次，道具外觀會不同（格局與擺位由 `layout.json` 決定，可以重現）。

## 授權注意

- FLUX.2 klein 只使用 **4B（Apache 2.0）**；9B 是非商用授權，腳本裡沒有提供。
- TRELLIS.2、RMBG-2.0 各有自己的授權條款，使用前請自行確認。
- 表面材質只從 [Poly Haven](https://polyhaven.com) 與 [ambientCG](https://ambientcg.com) 下載，全部為 CC0。
- 場景文字描述與其中的設定內容，著作權由提供者自行負責。
