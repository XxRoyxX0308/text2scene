# AGENT.md — 文字描述 → 遊戲引擎場景：agent 執行手冊

你是執行這條管線的 agent。照本文件由上而下做，每一步都有輸入、輸出與通過條件。
管線**沒有參考圖**：格局、物件清單、每個物件的 prompt 都由你讀文字描述後決定。
看圖的步驟（2D 候選圖、道具預覽、模組件預覽、引擎審查鏡頭）由你親自 Read 或 `look`，不要用檔案大小或程式輸出代替目視。

**核心分工**：資產在 Blender 做（無介面批次），場景在遊戲引擎組（Unreal 或 Unity），兩者之間只靠 `export/` 這個引擎中立的資料夾。
**場景不在 Blender 裡組裝**：不要把整個場景建成一個 .blend 再匯出（無法 instancing、無法剔除、光照到引擎全部不一樣、改一張桌子要重來）。

美術與效能的數字標準在 [docs/STANDARDS.md](docs/STANDARDS.md)，本文件只寫流程。

## 0. 任務定義

| | |
|---|---|
| **輸入** | 一份場景文字描述（`<場景描述>.md`）與範圍（例如「區域 A」）；`assets.json` 的 `target.engine` 決定引擎（`unreal` / `unity`），使用者也可能口頭指定 |
| **輸出** | `export/`（引擎中立的資產包與 `level.json`）、引擎裡的關卡（Unreal `/Game/T2S/Maps/<name>`，Unity `Assets/T2S/Scenes/<name>.unity`）、每台審查鏡頭的截圖 `export/shots/<engine>/*.png` |
| **完成條件** | 範圍內每個空間都建出來且連通；文字寫到的固定物件、標示、光源都在；道具報告沒有未處理的 `check` / `fail`；`budget.json` 沒有超標或超標項目已說明；每個空間至少一張審查截圖且你看過 |
| **回報** | 總結：空間表、物件表與做法、報告中被標記的項目與處理、與文字的差異、效能數字、耗時 |

路徑約定：`$P` = 專案資料夾在 WSL 內的路徑（例如 `/mnt/d/MyScene`），`$PW` = 同一路徑的 Windows 寫法；
`$R` = 本 repo（在 `wsl -e bash -lc '...'` 裡用 `/mnt/...` 寫法，在 PowerShell 與 Blender 裡用 Windows 寫法）。
專案資料夾（`$PW`）放 `assets.json`、`layout.json`、`work/`、`export/`；腳本都在 `$R/pipeline/`。

## 1. 環境與硬性限制

| 元件 | 位置 | 用途 |
|---|---|---|
| WSL Ubuntu + TRELLIS.2 | `~/TRELLIS.2`，conda env `trellis2` | 去背、2D → 3D |
| ComfyUI + FLUX.2 klein 4B | `~/ComfyUI`，conda env `comfyui` | 文字 → 2D 圖 |
| Blender 5.x | `t2s.config.json` 的 `blender` | **無介面批次**：道具烘焙、模組件 |
| Blender MCP（選用） | 使用者開著的 Blender | 只用來**調參**（步驟 5.1），不用來組場景 |
| Unreal 5.x 或 Unity 6 | `t2s.config.json` | 匯入與組關卡 |
| Windows Python 3 + numpy + Pillow | | 材質、貼花、layout |

硬性限制：

1. **GPU 只有 12 GB，ComfyUI 與 TRELLIS/RMBG 不能同時佔用。** 進入去背或 TRELLIS 前一定先跑 `comfy_stop.sh`。GPU 被佔用時不要跑 Blender 烘焙或引擎截圖。
2. **不要在 `trellis2` 環境安裝或升級任何套件。**
3. **下載或產生大檔前先看 Windows 磁碟實際剩餘空間**（Git Bash：`df -h /c /d`）；WSL 內的 `df` 不可信。
4. **不要用會比對到自己的 `pkill -f` / `pgrep -f`**；用 `comfy_stop.sh`，或把樣式第一個字母包成 `[x]`。
5. **不要印出 `~/.cache/huggingface/token`**，也不要列出含有它的程序指令列。
6. 長時間工作（TRELLIS、Blender 批次、引擎匯入）用背景執行，看日誌的結尾行。
7. **FLUX 只用 klein 4B（Apache 2.0）**，不要改回 9B。
8. **引擎專案是使用者的**：匯入器只寫 `/Game/T2S`（Unreal）或 `Assets/T2S`（Unity）。第一次對某個引擎專案匯入前先告訴使用者會寫入哪裡；不要動其他資料夾、專案設定或外掛（需要時請使用者決定）。
9. 引擎匯入會開編輯器視窗（Unreal 用 `-ExecCmds` 跑完自己關），執行前告訴使用者不要關掉它。
10. 在 Blender MCP 裡不要呼叫 `read_factory_settings` / `read_homefile`（會中斷 MCP）；`20_bake_props.py` 的 `tune()` 已經改用獨立的 `T2S_Tune` 場景。
11. 下載 CC0 材質前告訴使用者要下載什麼、來源、大小（`10_materials.py size`）；預設 2k，大面積近看的才用 4k。

時間參考（RTX 3060）：FLUX 冷啟動 2–4 分鐘、之後約 15 秒/張；TRELLIS 載入約 5 分鐘、每個物件 1–4 分鐘；
Blender 道具烘焙每個約 5–15 秒；模組件一整套 < 30 秒；Unreal 第一次開新專案編譯著色器 5–15 分鐘、之後匯入 1–3 分鐘。

## 2. 流程總覽

```
步驟 0  讀文字 → 空間配置（格線）、物件清單、材質清單、光線計畫
步驟 1  寫 assets.json（目標引擎、材質、模組件、道具、貼花）與 layout.json 的 rooms（先只有格局）
步驟 2  灰盒：layout → kit → engine，確認格局、動線、比例（此時沒有道具也沒關係）
步驟 3  2D 圖：逐物件迭代生成並審核（道具、平面影像、貼花）
步驟 4  選圖、去背、TRELLIS
步驟 5  道具：MCP 調參（代表性模型）→ 寫入 preset → 無介面批次 → 看報告，只處理被標記的
步驟 6  材質與貼花：CC0 掃描 / 平塗材質、貼花去背
步驟 7  完成 layout.json：道具擺位、散佈、貼花、燈光、後處理、鏡頭
步驟 8  layout（第二次，拿到真實面數）→ engine 匯入 → 審查截圖 → 對照文字修正（最多 3 輪）
步驟 9  總結回報
```

步驟 2 不需要 GPU，可以在等 TRELLIS 時做。組裝時發現缺物件就回步驟 1 補，已完成的會自動跳過。
`pipeline/run.ps1 <stage>` 把每一步包成一個指令（`images`、`models`、`materials`、`decals`、`layout`、`kit`、`props`、`engine`、`build`）。

## 3. 步驟 0：讀文字、定出計畫

1. 讀文字描述：先讀使用說明、共用元件、空間連接關係，再讀範圍內每個空間的小節。文件長就分段讀。
2. 每個空間記下：寬 × 深 × 高、出入口與通往哪裡、地面 / 牆面 / 天花材質與分割、固定物件與位置、標示文字、光源（位置、色溫、明暗）、狀態（新、使用中、破損）。
3. **定出格線配置**。所有空間放進同一個世界座標（+X 東、+Y 北，單位公尺），尺寸與原點對齊模組格（`kit.module`，預設 2 m）：
   - 文字尺寸不是模組倍數時取最接近的倍數，在總結裡註明差異。
   - 相鄰空間共用一條格線；門洞中心放在模組中點（`at` 用模組的奇數半格：1、3、5…，模組 2 m 時）。
   - 不規則空間用多個矩形（`rects`）。
4. **分類物件清單**：

   | 類別 | 例子 | 做法 |
   |---|---|---|
   | 建築 | 牆、地、天花、門洞、窗、柱、樓梯以外的結構 | 模組件（`kit`），由 `rooms` 自動產生 |
   | 生成道具 | 家具、設備、車輛、雕塑、盆栽、燈具 | `assets.json` 的 `props` → FLUX → TRELLIS → Blender 烘焙 |
   | 平面影像 | 海報、螢幕畫面、畫作、標語 | `props` 加 `"mode": "image_slab"` |
   | 貼花 | 污漬、水痕、裂縫、刮痕、手印、塗鴉 | `decals` |
   | 表面材質 | 混凝土、磨石、塗裝、金屬、玻璃 | `materials`（CC0 掃描或平塗） |
   | 植被、大片地形 | 草地、灌木叢、樹林、地形起伏 | **不在管線內**：交給引擎的 Foliage / PCG / Landscape（Unity Terrain），在總結中列出要使用者補的位置 |

5. **選物件的規則**
   - 文字寫到的一定要有；另外補讓空間可信的東西（垃圾桶、滅火器、推車、插座、出風口、紙箱）。
   - 重複出現的同款物件只列一次，在 `layout.json` 擺多個實例（引擎自動 instancing）。
   - 一個中等規模的區域（十幾個空間）通常 30–60 種道具、5–15 種貼花、6–15 種表面材質。

**TRELLIS 做不好的東西，一開始就改做法**（都是實際踩過的）：

| 物件 | 會發生什麼 | 做法 |
|---|---|---|
| 透明玻璃、玻璃罩、水槽 | 變成不透明白色實心 | 玻璃用模組件的窗或引擎材質；內容物單獨生成 |
| 帶透明部件的物件 | 透明部分變成白色 | 生成時不要那個部件，在引擎裡補 |
| 需要特定姿態的機械 | 姿態不對且無法事後調整 | prompt 寫明姿態並審核；不行就拆成兩個物件 |
| 細環、細線組成的雕塑 | 細構件遺失或黏在一起 | 主體生成，細件在 Blender 或引擎補 |
| 鏡面金屬 | 貼圖帶入環境色 | 形狀留用，在引擎換成金屬材質實例 |
| 文字、字母 | 重建成亂碼 | `image_slab`，或引擎的文字元件 |
| 蔓延的植物、樹根、藤蔓、蕨類 | 重建成一整塊 | 引擎 Foliage / PCG；牆面上的苔與污漬用貼花 |
| 大型平面（地毯、桌面以外的平板） | 厚度不對、邊緣破 | 直接用模組件或在 Blender 建簡單幾何 |

**輸出**：空間配置筆記與物件清單（寫在對話裡即可）。

## 4. 步驟 1：assets.json 與 layout.json

完整範例在 [examples/assets.json](examples/assets.json)、[examples/layout.json](examples/layout.json)，從那裡複製再改。

### 4.1 assets.json

```jsonc
{
  "description": "一句話：哪個區域、整體風格",
  "target": {"engine": "unreal", "unity_pipeline": "urp"},     // unreal | unity
  "generation": {"candidates": 4, "megapixels": 1.0,
                 "suffix": "...白底單一物件...", "decal_suffix": "...白底俯拍的痕跡..."},
  "materials": [ ... ],      // 4.2
  "kit": { ... },            // 4.3
  "props": [ ... ],          // 4.4
  "decals": [ ... ],         // 4.5
  "bake_presets": { },       // 步驟 5.1 調好的參數寫在這裡
  "budgets": { }             // 覆寫 docs/STANDARDS.md 的預算
}
```

### 4.2 materials（表面材質）

| 欄位 | 說明 |
|---|---|
| `id` | 名稱，模組件與 layout 用它引用 |
| `source` | `polyhaven:<id>`、`ambientcg:<id>`（CC0 掃描，用 `10_materials.py search` 找）、`flat`（塗裝、烤漆）、`glass` |
| `res` | `1k` / `2k` / `4k`（掃描用） |
| `tile_m` | 幾公尺鋪一次；能整除模組尺寸 |
| `color` / `roughness` / `metallic` / `noise` | `flat` 與 `glass` 用；`noise` 是顏色與粗糙度的擾動量（預設 0.06） |
| `tint`、`roughness_scale`、`normal_strength` | 掃描材質的微調 |
| `macro`、`detail` | 引擎端打破重複的強度（預設 0.12 / 0.4） |
| `opacity` | `glass` 用 |

### 4.3 kit（模組件）

```jsonc
"kit": {
  "module": 2.0, "wall_thickness": 0.2, "bevel": 0.012,
  "door": {"width": 1.2, "height": 2.3},
  "window": {"width": 1.5, "height": 1.8, "sill": 0.6},
  "sets": {
    "alsi": {"wall": "paint_white", "trim": "steel_brushed", "floor": "terrazzo", "ceiling": "ceiling_panel",
             "glass": "glass_clear", "exterior": "concrete", "baseboard": 0.1}
  }
}
```

一個 `set` 是一套視覺語彙（例如地表 / 地下淺層 / 深層各一套）。材質是預設值，`rooms` 可逐個空間覆寫。
需要哪些件（牆的高度、門、窗、柱）由 `30_layout.py` 從 `rooms` 算出，你不用列。

### 4.4 props（道具）

| 欄位 | 說明 |
|---|---|
| `id`、`prompt` | 小寫英文加底線；prompt 用英文，只描述物件本身（見步驟 3 的原則） |
| `height_m` / `width_m` / `depth_m` | 真實尺寸（擇一），烘焙時縮放用，**必填** |
| `category` | `hero` / `prop` / `small`：決定貼圖密度、LOD 數、預設 preset |
| `condition` | `new` / `used` / `worn` / `ruined`：髒污與磨損程度；或直接給 `wear` / `dirt` / `rough_var` / `bare_metal` |
| `preset`、`bake` | 烘焙參數組與逐物件覆寫（步驟 5） |
| `yaw_fix` | 模型正面不是朝 -Y 時，轉幾度（看 preview.png 決定） |
| `collision` | `box`（預設）/ `convex` / `none` |
| `emissive` | 自發光強度（螢幕、燈箱） |
| `trellis` | `faces`（LOD0 目標面數）、`texture`、`high_faces`（主角道具設 200000–300000 以烘焙法線）、`pipeline_type`、`seed` |
| `mode: "image_slab"` + `aspect`、`frame` | 平面影像；需要自己的 `suffix`（正面、滿版、無邊框、無透視） |
| `status: "rejected"` + `note` | 放棄並寫明替代做法 |

### 4.5 decals（貼花）

| 欄位 | 說明 |
|---|---|
| `id`、`prompt` | 描述痕跡本身（稀疏、邊緣柔和），共用 `decal_suffix` 要求白底俯拍 |
| `size_m` | 實際尺寸 [寬, 高] |
| `alpha` | `luma`：白底上的深色痕跡（污漬、水痕、裂縫、煤灰）；`matte`：有明確形狀或淺色的（紙張、油漆、標誌、落葉），走 RMBG 去背 |
| `roughness`、`opacity`、`normal_strength` | 水漬粗糙度低（0.2），灰塵高（0.8） |

## 5. 步驟 2：灰盒

先只寫 `layout.json` 的 `rooms`（加上最基本的 `lighting`），跑：

```powershell
.\pipeline\run.ps1 layout -Project $PW     # 會印出需要的模組件數與警告
python $R\pipeline\12_shared.py $PW
python $R\pipeline\10_materials.py build $PW
.\pipeline\run.ps1 kit -Project $PW
.\pipeline\run.ps1 engine -Project $PW -Shots
```

- 看 `export/kit/preview_<set>.png`：每種件都在、門窗方向正確。
- 看審查截圖：格局、動線、門洞位置、天花高度與文字一致；沒有空間重疊（`30_layout.py` 會警告 overlap）。
- 匯入器第一步會匯入 `SM_T2S_Axes` 並檢查座標軸；出現 `axis calibration MISMATCH` 就停下來，不要繼續組關卡。

### 5.1 layout.json 的 rooms

```jsonc
{"id": "LOBBY", "kit": "alsi", "origin": [0, 0], "yaw": 0, "size": [12, 10, 5],
 "openings": [{"wall": "S", "at": 7, "kind": "door", "to": "PLAZA"},
              {"wall": "S", "at": 1, "kind": "window"},
              {"wall": "E", "at": 5, "kind": "open", "width": 2, "height": 3.0, "to": "CORR"}],
 "floor": "terrazzo"}          // wall / ceiling / trim / exterior / glass 也可覆寫
```

- `origin` 是空間西南角（`[x, y]` 或 `[x, y, z]`，多樓層用 z）；`yaw` 只能是 90 的倍數；`size` = [寬(X), 深(Y), 高]。
- 牆、門、窗以空間自己的座標描述：`wall` 是 `S`/`N`/`W`/`E`，`at` 是開口中心離該牆起點（南北牆從西端、東西牆從南端）的距離。
- `kind`：`door`（門洞與門框，門扇另外當道具擺）、`window`、`open`（寬開口，`width` 為模組倍數，`height` 以上補橫樑）。
- 兩個空間共用的牆只會建一次，兩面各用自己空間的牆面材質；只有一側有空間的牆，外側用 `exterior` 材質。
- `outdoor: true`：沒有天花，戶外對戶外的邊界不建牆。`ceiling: false`：室內挑空。
- 牆的轉角、T 字接點、自由端會自動補柱。

## 6. 步驟 3：迭代生成 2D 圖（核心步驟）

啟動 ComfyUI（整個步驟 3 期間保持開啟）：

```bash
wsl -e bash -lc 'bash $R/pipeline/comfy_start.sh'     # 印出 COMFY_READY 才算成功
```

對**每個物件**（道具、平面影像、貼花）一次生成一張，迭代到合格：

```
loop:
  生成一張  →  01_generate.py <專案> --next <id> [--prompt "..."] [--review-prev "..."]
  Read 這張 cand_n.png，依審核清單寫評語
  有缺點    →  針對缺點改寫 prompt，下一輪帶 --prompt 與 --review-prev
  已夠好    →  不改 prompt，下一輪只帶 --review-prev（換種子多拿一張備選）
  結束      →  ≥ 4 張且至少 1 張合格；8 張仍不合格就選最接近的並在總結註明
```

```bash
wsl -e bash -lc 'python3 $R/pipeline/01_generate.py $P --next office_chair'
wsl -e bash -lc 'python3 $R/pipeline/01_generate.py $P --next office_chair --review-prev "castors cut off" --prompt "... all five castors fully inside the frame ..."'
```

- 每次呼叫記錄在 `work/props/<id>/prompts.json`（貼花在 `work/decals/<id>/`）。定稿的 prompt 寫回 `assets.json`。
- prompt 穩定、物件很多時可以先批次（`01_generate.py $P [id ...]`，同 prompt 換種子 4 張），只對不合格的走迭代。

**審核清單**

| 項目 | 不合格的樣子 |
|---|---|
| 單一物件 | 多了別的東西、多一個同款、有人 |
| 完整 | 被畫面邊緣裁掉、沒有留白 |
| 符合描述 | 種類、顏色、形狀、部件數量、姿態不符 |
| 風格一致 | 和同空間其他物件不是同一個世界（太卡通、太復古、色彩體系不對） |
| 背景乾淨 | 不是純白、有地面、有明顯投影 |
| 適合 3D | 看不出厚度（平面影像除外）、極端透視、細長部件糊在一起、透明部件 |
| 文字（平面影像） | 多出文字描述沒有的字、亂碼 |
| 貼花 | 背景不是純白、痕跡太密太均勻（要稀疏、不規則）、邊緣切齊畫框 |

**寫 prompt 的原則**：英文一段話，只寫這一個物件；寫出種類、形狀、材質、顏色、關鍵部件、狀態與大致尺寸；
同一區域用同一套材質與色彩語彙；細長鏤空的要求 "chunky solid forms"；不要在物件上要求文字或標誌。
改 prompt 一次只針對看到的缺點，保留有效的描述；已合格就不要再改。

全部完成後：`wsl -e bash -lc 'bash $R/pipeline/comfy_stop.sh'`（印出 `COMFY_STOPPED` 才能進下一步）。

## 7. 步驟 4：選圖、去背、TRELLIS

```bash
wsl -e bash -lc 'source ~/miniconda3/etc/profile.d/conda.sh; conda activate trellis2; python $R/pipeline/02_select_matte.py $P sheet'
```

Read 每個 `sheet.png`，寫 `work/selection.json`：`{"office_chair": {"pick": 0, "why": "五個輪子都清楚；cand 2 椅背變形"}, ...}`（道具、平面影像、貼花都要）。

```bash
wsl -e bash -lc '... conda activate trellis2; python $R/pipeline/02_select_matte.py $P matte'
```

只對需要去背的做（TRELLIS 道具、`alpha: matte` 的貼花）。Read 每個 `review.png`：物件完整、無殘影白邊、細件沒被吃掉、白色物件沒被挖空；不合格就換一張重跑 `matte <id>`。

```bash
wsl -e bash -lc 'bash $R/pipeline/trellis_loop.sh $P 8 > $P/work/trellis.log 2>&1'     # 背景執行
```

監看 `work/trellis.log` 的 `[done]` / `[FAIL]` / `[all done]`。同一物件反覆失敗多半是去背圖有問題，回上一步換圖。
`trellis.faces` 就是 LOD0 面數（見 docs/STANDARDS.md 3.2）；主角道具設 `high_faces` 會多輸出一個高模供烘焙法線。

## 8. 步驟 5：道具（調參 → 批次 → 報告）

不要一個指令一個指令地在即時視窗裡處理每個模型（慢又不穩）。流程是：

### 8.1 用代表性模型調參（Blender MCP，即時）

每一類（雜物、硬表面、有機、主角）挑 1–2 個代表，在使用者開著的 Blender 裡跑同一份程式：

```python
# execute_blender_code
import runpy
g = runpy.run_path(r"$R\pipeline\20_bake_props.py")
print(g["tune"](r"$PW", "barricade"))                                   # 先用預設
print(g["tune"](r"$PW", "barricade", decimate="planar", planar_angle=3, cage=0.012, delight=0.3))
```

- 結果留在 `T2S_Tune` 場景（使用者的其他場景不動）；用 `look`（`mode="angles"`、`shading="material"`）看 LOD0，也 Read `export/props/SM_<id>/preview.png`。
- 回傳值有面數、內殼刪除數、AO 全黑比例、法線烘焙錯誤比例、LOD 輪廓變化與標記（`flags`）。
- 參數穩定後寫進 `assets.json`：整類用 `"bake_presets": {"hard_surface": {...}}`，單一物件用該道具的 `"bake": {...}`。
- MCP 連不上時，同樣的 `tune()` 也能用無介面 Blender 跑（`blender -b -P <小腳本>`），只是看不到即時畫面，改看 preview.png。

可調的參數（`20_bake_props.py` 的 `PRESETS`）：

| 參數 | 作用 | 什麼時候動 |
|---|---|---|
| `hidden` | 刪除封閉內殼（射線出不去的連通塊） | 預設開；幾乎不用關 |
| `interior` | 刪除內部面 | 預設開 |
| `normals` | `auto`：只在射線測試證明有改善時才重算法線；`recalc` / `keep` | 報告有「faces point inward」時 |
| `remesh` | 體素重建（物件尺寸的比例，例如 0.006），會強制重展 UV 並轉烘貼圖 | 網格爛、雙層牆、AO 大片全黑 |
| `faces` | LOD0 面數上限 | TRELLIS 面數過多時（優先改 `trellis.faces` 重跑） |
| `decimate` / `planar_angle` | `planar`：先合併平面（硬表面不跑形）；`collapse`：有機 | LOD 輪廓變形時 |
| `uv` | `keep`（TRELLIS 的 UV）/ `smart`（重展並轉烘） | UV 浪費嚴重、貼圖密度太低 |
| `cage` / `ray` | 烘焙 cage 外擴與射線距離（物件尺寸的比例） | 法線烘焙出現黑斑、穿透 |
| `delight` | 壓平 AI 貼圖烘進的光影（0–1） | 頂面亮底面暗的貼圖 |
| `albedo_normal` | 沒有高模時，從基色細節做微法線的強度 | 表面太光滑像模具 |
| `tier` | `auto`：報告 ok 就信任；`review`：一律要人看 | 主角道具用 `review` |

### 8.2 無介面批次

```powershell
.\pipeline\run.ps1 props -Project $PW                 # 全部
.\pipeline\run.ps1 props -Project $PW -Ids a,b         # 部分
.\pipeline\run.ps1 props -Project $PW -Retexture       # 只重做髒污 / 磨損 / 匯出（沿用烘焙結果，很快）
```

### 8.3 看報告，只處理被標記的

`export/props/bake_report.md`（完整資料在 `work/bake_report.json`）每列一個道具：原始面數、LOD 面數、貼圖尺寸與密度、AO 全黑比例、法線錯誤比例、標記。

| 狀態 | 處理 |
|---|---|
| `ok`（tier auto） | 不用看 |
| `review`（tier review，例如主角道具） | Read preview.png，確認正面方向（`yaw_fix`）、比例、髒污程度；必要時回 8.1 |
| `check` | 依標記處理：AO 全黑 → 試 `remesh`；法線錯誤 → 調 `cage` / `ray`；LOD 變形 → `planar` 或降低比例；密度太低 → `uv: smart` 或提高 `trellis.texture` |
| `fail` | 看錯誤訊息；多半是 GLB 有問題，回步驟 4 換種子或換圖 |

每個物件最多重做 2 次，之後接受並在總結註明。所有 preview.png 也都看一眼正面方向：模型正面不是朝 -Y 就設 `yaw_fix`。

## 9. 步驟 6：材質與貼花

```powershell
python $R\pipeline\10_materials.py search concrete floor        # 找 CC0 掃描
python $R\pipeline\10_materials.py size polyhaven:concrete_floor_02 2k
python $R\pipeline\10_materials.py build $PW
python $R\pipeline\11_decals.py $PW
python $R\pipeline\12_shared.py $PW
```

- 材質的 `texel_density` 低於 512 會警告（近看會糊）：換更高解析度或縮小 `tile_m`。
- 貼花看 `export/decals/<id>/T_<id>_BC.png`（帶透明）：邊緣要淡出、不能看到方形邊框。

## 10. 步驟 7：完成 layout.json

```jsonc
"props":   [{"asset": "reception_desk", "room": "LOBBY", "pos": [6, 6.5], "yaw": 0},
            {"asset": "lounge_sofa", "room": "LOBBY", "against": "W", "at": 4},          // 背靠西牆、面向室內
            {"asset": "screen_lobby", "room": "LOBBY", "against": "N", "at": 6, "z": 1.6}],   // 牆面掛件：z 是底邊高度
"scatter": [{"room": "OFFICE", "assets": ["paper_stack", "trash_bin"], "count": 6, "zone": "edges", "seed": 3}],
"decals":  [{"asset": "floor_scuffs", "room": "LOBBY", "on": "floor", "pos": [7, 2], "yaw": 20},
            {"asset": "wall_grime", "room": "CORR", "on": "wall:W", "pos": [3, 0.6]}],    // 牆面：[沿牆距離, 高度]
"lights":  [{"room": "LOBBY", "type": "rect", "grid": {"spacing": [3, 3]}, "size": [1.2, 0.3], "lumens": 2400,
             "kelvin": 4500, "mobility": "static", "fixture": "ceiling_light"},
            {"room": "LOBBY", "type": "spot", "pos": [6, 8.5, 4.9], "dir": [0, 0.35, -1], "cone": 50,
             "lumens": 3000, "kelvin": 5600, "shadows": true, "mobility": "stationary"}],
"lighting": {"gi": "dynamic", "sun": {"dir": [-0.45, 0.55, -0.7], "lux": 75000, "kelvin": 5800}, "sky": true,
             "fog": {"density": 0.015, "height_falloff": 0.25, "volumetric": true},
             "post": {"exposure": [6, 14], "bloom": 0.35, "vignette": 0.25, "grain": 0.03, "saturation": 0.95,
                      "contrast": 1.05, "white_balance": 6200, "ao": 0.5}},
"cameras": [{"id": "LOBBY_v1", "room": "LOBBY", "pos": [7, 1.2, 1.6], "look": [6, 9, 1.8]}],
"player_start": {"room": "PLAZA", "pos": [8, 2, 0], "yaw": 0}
```

- 座標都是空間自己的座標（原點在西南角），yaw 是物件從「正面朝 -Y」逆時針轉的角度；空間的 `yaw` 會自動套上。
- `against` 依道具的實際深度把背面貼在牆上；需要離牆一點就加 `gap`。牆面掛件（`image_slab`）自動貼牆，`z` 是底邊高度。
- 道具的位置要合理：椅子在桌邊、正面朝桌子；垃圾桶在牆角；不要擋門（`scatter` 會自動避開門口 1 m）。
- `scatter` 的 `zone`：`floor`（隨機分布、隨機轉向）或 `edges`（沿牆、朝向室內、小角度抖動）；放不下會警告實際數量。
- 燈：`grid` 把一個定義鋪滿天花（`spacing`、`margin`），`fixture` 在每盞燈的位置放燈具道具；`dir` 是光的方向（預設朝下）。
  有陰影的燈每個空間 ≤ 4 盞（重疊上限），其餘關陰影。
- `gi`：`dynamic`（Unreal Lumen、所有燈 Movable；Unity 即時燈 + 探針）或 `baked`（光照貼圖，需要烘焙）。
- `cameras`：每個空間至少一台審查鏡頭（沒寫的空間會自動在南側入口朝北放一台）；大空間多放幾台（回望、側面、主角道具近景）。

**填滿的要求**：玩家看得到的地方不能有大面積空平面（約 3 × 3 m 以上沒有任何東西打斷）。
用散佈、貼花、牆面掛件（海報、螢幕、標示、滅火器箱、開關）、天花設備（燈、出風口、偵測器）把它補滿，但要符合空間用途與狀態。

## 11. 步驟 8：匯入引擎與驗收

```powershell
.\pipeline\run.ps1 layout -Project $PW          # 第二次：用真實面數算預算
.\pipeline\run.ps1 engine -Project $PW -Shots
```

- **Unreal**：`t2s_unreal.py` 建主材質（`M_T2S_Prop` / `Surface` / `Glass` / `Decal`）、材質實例、匯入網格（不透明的開 Nanite）、
  在 `/Game/T2S/Maps/<name>` 生成所有實例、燈、太陽、天空、高度霧、後處理、反射捕捉、貼花、鏡頭與 PlayerStart。
  日誌在 `export/unreal_import.log`。重跑時只重匯入有變更的檔案，關卡中帶 `T2S` 標籤的 actor 會重建，使用者手動加的不受影響。
  日誌出現 `WARNING project setting ...`（例如 Mesh Distance Fields 關閉，Lumen 沒有間接光、天花全黑）時告訴使用者，由使用者決定是否改專案設定。
- **Unity**：`T2SImporter.cs`（`run.ps1` 會複製到專案的 `Assets/T2S/Editor/`）依 URP / HDRP / Built-in 建材質、
  帶 LODGroup 與碰撞的 prefab、場景、燈、後處理 Volume、探針、貼花（URP 需要 Renderer 上有 Decal 功能）。日誌在 `export/unity_import.log`。
  URP / Built-in 的燈沒有物理單位（流明是近似換算），截圖太暗或太亮先調 `lumens` 或太陽的 `lux`；
  Unity 端的間接光與反射探針要烘焙才有（`Unity.exe ... -t2sBake`，或在編輯器 Lighting 視窗烘焙）。
  Unity 專案路徑不要太深：Windows 路徑上限 260 字元，太深時 URP 套件裡的部分 Shader Graph 範本會載入失敗。

**驗收**（文字就是標準）：一個空間一個空間，重讀文字小節，對著審查截圖逐項核對，回到 `layout.json` 或資產修正，最多 3 輪：

- 尺寸、格局、每個出入口與通往的空間；
- 每個固定物件存在、位置與朝向正確；
- 標示文字；光源、色溫、明暗；狀態細節；
- 三個距離都有細節（docs/STANDARDS.md 第 4 節）、沒有大面積空平面；
- 沒有懸空或穿插（道具底面在地上、牆面掛件貼牆）；
- `budget.json` 沒有未處理的超標。

截圖太暗或太亮時調 `post.exposure` 或燈的 `lumens`，不要用後處理硬拉。

## 12. 步驟 9：總結回報

- 空間表：空間、尺寸（與文字的差異）、使用的模組套件、審查鏡頭。
- 道具表：做法（TRELLIS / image_slab）、2D 張數與 prompt 修改次數、preset 與覆寫、LOD0 面數、貼圖密度、報告狀態。
- 被標記的項目與處理結果；放棄或妥協的項目與原因。
- 表面材質：來源（Poly Haven / ambientCG asset id 或 flat）、貼圖密度。
- 自己補上的東西（文字沒寫、為了可信或填滿而加的）。
- 需要使用者在引擎裡補的：植被、地形、門扇動畫、互動。
- `budget.json` 的總數：實例、LOD0 三角面、貼圖記憶體、材質數；超標項目。
- 各階段耗時（`work/generation_log.jsonl`、`work/trellis.log`、`bake_report.json` 的 seconds）。
- 確認 ComfyUI 與 TRELLIS 都已停止。

只回報實際看過、驗證過的結果；沒檢查的要明說。

## 13. 檔案對照

```
<場景描述>.md                       輸入：文字描述（使用者提供）
assets.json / layout.json           你撰寫：要做什麼 / 怎麼組
t2s.config.json（repo 根目錄）       工具路徑（從 examples/ 複製）

pipeline/01_generate.py             FLUX.2 klein 4B 文字生圖（--next 迭代 / 批次）
pipeline/comfy_t2i.py               ComfyUI API 客戶端
pipeline/comfy_start.sh|stop.sh     啟動 / 停止 ComfyUI
pipeline/02_select_matte.py         對照表與去背
pipeline/03_trellis_batch.py        TRELLIS.2 → model.glb（+ model_high.glb）
pipeline/trellis_loop.sh            TRELLIS 自動重啟迴圈
pipeline/10_materials.py            CC0 掃描（Poly Haven、ambientCG）與平塗材質
pipeline/11_decals.py               貼花去背、法線
pipeline/12_shared.py               macro variation、細節法線、預設貼圖
pipeline/20_bake_props.py           Blender：道具清理、烘焙、髒污、LOD、碰撞、報告（tune / 批次）
pipeline/21_build_kit.py            Blender：模組件 + 座標軸校正件
pipeline/30_layout.py               layout.json → level.json、kit_needed.json、budget.json
pipeline/textures.py, t2s.py        共用函式
pipeline/engines/unreal/t2s_unreal.py   Unreal 匯入器
pipeline/engines/unity/Editor/T2SImporter.cs  Unity 匯入器
pipeline/contact_sheet.py           把截圖拼成一張總表
pipeline/run.ps1                    各步驟的指令包裝

work/props/<id>/      cand_*.png、prompts.json、sheet.png、rgba.png、model.glb、bake/
work/decals/<id>/     cand_*.png、prompts.json
work/materials/<id>/  下載的掃描原檔
work/selection.json   選圖結果（你撰寫）
work/bake_report.json 道具報告（完整）
export/               引擎讀的全部東西：shared/、materials/、decals/、props/、kit/、level.json、budget.json、shots/
```
