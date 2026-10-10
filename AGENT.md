# AGENT.md — 文字描述 → Blender 場景：agent 執行手冊

你是執行這條管線的 agent。照本文件由上而下做，每一步都有明確的輸入、輸出與通過條件。
這條管線**沒有參考圖**：場景的格局、物件清單、每個物件的 prompt 都由你讀文字描述後自己決定。
「看圖」的步驟（審核 2D 圖、檢視 3D 模型、檢查場景）由你親自用 Read 或 `look` 判斷，不要跳過，也不要用檔案大小或程式輸出代替目視檢查。

## 0. 任務定義

| | |
|---|---|
| **輸入** | 一份場景文字描述（例如專案根目錄的 `<場景描述>.md`）與要做的範圍（例如「區域 A」）；使用者可能另外指定一個補充資料夾（世界觀、標誌圖檔） |
| **輸出** | `scene.blend`（貼圖已打包）、每個空間的第一人稱渲染 `work/renders/view_<空間>.png`、`view_overview.png`、`contact_sheet.png` |
| **完成條件** | 範圍內每個空間都建出來且彼此連通；文字寫到的固定物件、標示文字、光源都在場景裡；物件走 FLUX.2 → TRELLIS.2（每個物件至少審核過 4 張 2D 圖、每個 3D 模型都檢視過並已減面）；**玩家看得到的地方沒有大面積的空平面**；每個空間至少有一張第一人稱渲染 |
| **回報** | 結尾交付一份總結：物件清單與做法、哪些不合格及原因、與文字描述的差異、耗時 |

路徑約定：`$P` = 專案在 WSL 內的路徑（例如 `/mnt/d/text2scene`）；`$PW` = 同一路徑的 Windows 寫法。

## 1. 環境與硬性限制

| 元件 | 位置 | 呼叫方式 |
|---|---|---|
| WSL Ubuntu | 使用者虛擬磁碟在 D 槽（HDD） | `wsl -e bash -lc '...'` |
| TRELLIS.2 | `~/TRELLIS.2`，conda env `trellis2` | 由 `pipeline/trellis_loop.sh` 呼叫 |
| ComfyUI + FLUX.2 klein 4B | `~/ComfyUI`，conda env `comfyui` | `pipeline/comfy_start.sh` / `comfy_stop.sh` |
| Blender 5.2 | 使用者開著的 Blender，已啟用 MCP for Blender 外掛 | Blender MCP 工具：`get_addon_status`、`get_scene_info`、`execute_blender_code`、`look` |

硬性限制，違反會出事：

1. **GPU 只有 12 GB，ComfyUI 與 TRELLIS/RMBG 不能同時佔用。** 生成 2D 圖期間開 ComfyUI；進入去背或 TRELLIS 之前一定先跑 `comfy_stop.sh`。
2. **不要在 `trellis2` 環境安裝或升級任何套件。**
3. **下載或產生大檔前，先看 Windows 磁碟實際剩餘空間**（Git Bash：`df -h /c /d`）。WSL 內的 `df` 顯示的是虛擬上限，不可信。
4. **不要用會比對到自己的 `pkill -f` / `pgrep -f`**（指令字串本身含有樣式時會殺掉自己）。用 `comfy_stop.sh`，或把樣式第一個字母包成 `[x]`。
5. **不要印出 `~/.cache/huggingface/token` 的內容**，也不要列出含有該 token 的程序指令列。
6. 長時間工作（TRELLIS）用背景執行，並監看 `work/trellis.log` 的 `[done]` / `[FAIL]` 行。
7. **Blender 場景只透過 Blender MCP 建立與修改。** 不要在磁碟上寫 `.py` 建置腳本，也不要用 `blender -b -P` 在背景跑；所有建模、擺位、材質、燈光、渲染都由你用 `execute_blender_code` 在使用者開著的 Blender 裡一步一步做，用 `look` 看結果。MCP 連不上時停下來告訴使用者，不要改走背景 Blender。
8. ComfyUI 或 TRELLIS 佔用 GPU 期間，`look` 只用 `solid` / `wireframe` / `material` 著色，不要用 `rendered`，也不要渲染。
9. 不要在 MCP 裡呼叫 `bpy.ops.wm.read_factory_settings` 或 `read_homefile`：會重置工作階段並中斷 MCP。要清空場景就逐一刪除物件與資料。
10. **FLUX 只用 klein 4B（Apache 2.0，可商用），不要改回 9B。** TRELLIS 預設用 512 管線；檢視後覺得某個模型不夠精細，可以對該物件改用 `1024_cascade`（見步驟 4）。

時間參考（RTX 3060）：ComfyUI 冷啟動後第一張圖 2–4 分鐘，之後約 15 秒/張；TRELLIS 載入約 5 分鐘，512 管線每個物件 1–4 分鐘。

## 2. 流程總覽

```
步驟 0  確認 Blender MCP 可用 → 讀文字描述 → 寫下空間配置與物件清單
步驟 1  寫 scene.json（物件與 prompt）
步驟 2  逐物件迭代生成 2D 圖：生成 → 看圖 → 決定是否改 prompt → 再生成（至少 4 張）
步驟 3  寫 work/selection.json → 02_select_matte.py matte → 檢查去背
步驟 4  trellis_loop.sh → 用 MCP 把每個 GLB 匯入 Blender → look 逐一檢視 → 減面
步驟 5  用 MCP 組裝場景：配置 → 建築 → 擺放模型 → 填滿空面 → 材質 → 燈光 → 相機與渲染
步驟 6  總結回報
```

步驟 1–4 與步驟 5 可以交錯：建築不需要 GPU，等 TRELLIS 的時候就可以先在 Blender 裡建。
組裝時發現還缺物件，就回步驟 1 把它加進 `scene.json` 再跑一輪（已完成的物件會自動跳過）。

## 3. 步驟 0：確認 MCP、讀文字、定出計畫

1. **先確認 MCP 能用。** `get_addon_status`、`get_scene_info`；場景裡若只有 Blender 預設的 Cube / Light / Camera，用 `execute_blender_code` 把預設 Cube 刪掉，確認指令真的生效。
   場景裡若已有上一次的成果，先看清楚再決定接著做還是清掉重來（清空前要問使用者）。
2. **讀文字描述。** 先讀文件開頭的使用說明、共用元件與空間連接關係，再讀範圍內每個空間的小節。文件很長時分段讀，不要只讀標題。
   使用者有指定補充資料夾時，需要更細的設定（公司標誌、世界觀、色彩）就去那裡查；真實的標誌圖檔直接拿來用，不要另外生成。
3. **對每個空間記下**：寬 × 深 × 高、出入口與各自通往哪裡、地面 / 牆面 / 天花的材質與分割、固定物件與位置、標示文字的確切內容、光源、狀態細節。
4. **定出整體配置。** 依連接關係把所有空間排進同一個世界座標（見步驟 5.1）。文字沒寫的尺寸與位置由你決定，但要讓門對得上門、走廊接得起來。
5. **列出物件清單**，分成三類：

   | 類別 | 例子 | 做法 |
   |---|---|---|
   | 生成物件 | 家具、設備、植物、雕塑、車輛、道具 | FLUX.2 → TRELLIS.2（`scene.json`） |
   | 平面影像 | 海報、螢幕畫面、照片、畫作、標語牆 | FLUX.2 生成正面圖，Blender 做成薄板（`"mode": "image_slab"`） |
   | 建築與規則幾何 | 地板、牆、天花、門窗、樓梯、櫃台、層架、置物櫃、花台、玻璃容器、文字標示 | 步驟 5 直接在 Blender 建，不寫進 `scene.json` |

**選物件的規則**

- 文字寫到的物件一定要有；另外自己補上讓空間可信的東西（垃圾桶、滅火器、盆栽、推車、飲水機、清潔工具、影印機……）。場景要複雜，寧可多不要少。
- 整個範圍至少 10 種生成物件；一個中等規模的區域（十幾個空間）通常需要 40 種以上。
- 重複出現的同款物件只列一次，步驟 5 用同一個模型的複本擺放。
- 清單不必一次列完：一邊組裝一邊想還缺什麼，缺了就補。

**TRELLIS 做不好的東西，一開始就改用別的做法**（都是實際踩過的）：

| 物件 | 會發生什麼 | 做法 |
|---|---|---|
| 透明玻璃容器、玻璃罩、水槽 | 變成不透明白色實心筒，裡面的東西消失 | 玻璃在 Blender 用玻璃材質建；內容物單獨當一個物件生成 |
| 帶透明部件的物件（閘門的玻璃翼板、櫥窗） | 去背後透明部分變成不透明白色 | 生成時不要那個部件，改在 Blender 用玻璃材質補上 |
| 需要特定姿態的機械（貨叉升起的堆高機） | 2D 圖常常還是預設姿態，3D 模型無法事後調整 | prompt 明寫姿態並在審核時確認；不行就拆成兩個物件（車體、貨叉與載物），在 Blender 裡組合 |
| 由細環、細線組成的複合雕塑 | 細長構件遺失或黏在一起 | 主體生成，細環用 Blender 的 Torus / 曲線補 |
| 鏡面金屬 | 烘焙貼圖帶入環境色（偏金、偏灰） | 形狀留用，材質換成 Blender 的金屬材質 |
| 文字、字母 | 重建成亂碼 | `image_slab`，或用 Blender 文字物件 |

**輸出**：你自己的空間配置筆記與物件清單（寫在對話裡即可，不需要另存檔案）。

## 4. 步驟 1：撰寫 scene.json

`scene.json` 只描述「要生成哪些物件」（步驟 2–4 的腳本會讀）；Blender 場景怎麼組裝不寫在這裡。

```jsonc
{
  "description": "一句話描述場景：哪個區域、來源文件、整體風格",
  "camera": {"hfov_deg": 90, "height_m": 1.6},     // 第一人稱相機的設定，步驟 5.7 使用
  "generation": {
    "candidates": 4,            // 批次模式每個物件的張數
    "megapixels": 1.0,
    "suffix": "The single object alone, centered, the whole object fully inside the frame with empty margin on all sides. Three-quarter view from slightly above eye level. Pure white seamless background, flat even studio lighting, no cast shadow, no floor, no people. Photorealistic product photograph, clean brand-new condition."
  },
  "objects": [
    {
      "id": "office_chair",                // 小寫英文加底線，也是資料夾名與 Blender 物件名
      "prompt": "A modern black ergonomic office task chair with a black mesh backrest, ...",   // 英文，只描述物件本身
      "height_m": 1.0,                     // 真實高度，步驟 5.3 縮放用
      "trellis": {"faces": 30000, "texture": 2048}
    },
    {
      "id": "poster_safety",
      "mode": "image_slab",
      "aspect": 0.71,                      // 寬 ÷ 高
      "suffix": "Flat straight-on print filling the whole frame edge to edge, no border, no perspective.",
      "prompt": "A minimalist workplace safety poster ..."
    }
  ]
}
```

物件的可選欄位：

| 欄位 | 用途 |
|---|---|
| `height_m` | 物件的真實高度（公尺）；寬的東西可改記 `width_m`。只是給你自己擺位時讀的紀錄，腳本不會用 |
| `mode: "image_slab"` | 平面影像：不跑 TRELLIS，步驟 5 用 2D 圖做成薄板 |
| `aspect` | 圖片的寬 ÷ 高（預設 1）。`image_slab` 依實際比例設定；細高或扁長的立體物件也可以設，讓物件佔滿畫面 |
| `suffix` | 取代 `generation.suffix`。`image_slab` 一定要設（共用 suffix 是給白底立體物件用的） |
| `seed_base` | 種子起點（預設 1000），想整組重來時換一個 |
| `trellis.faces` | GLB 的目標面數（預設 30000），依步驟 4 的減面表設定 |
| `trellis.texture` | 貼圖邊長（預設 2048）；小道具用 1024 |
| `trellis.pipeline_type` | 預設 `"512"`；模型不夠精細時對該物件設 `"1024_cascade"` 重跑 |
| `trellis.seed` | 3D 重做時換種子 |
| `status: "rejected"` | 放棄這個物件（TRELLIS 會跳過），並用 `note` 寫明原因與替代做法 |

**寫 prompt 的原則**（沒有參考圖，prompt 是唯一的資訊來源）

- 用英文，一段話，只寫這一個物件：種類、形狀、材質、顏色、關鍵部件、狀態（全新 / 老舊 / 損壞），並寫出大致尺寸（「about 1.2 metres tall」），比例會比較對。
- 風格要跟空間一致：同一個區域的物件用同一套材質與色彩語彙（從文字描述的視覺體系章節取）。
- 白底、平光、三分之四視角這類共用要求放在 `generation.suffix`，不要每個 prompt 重複寫。
- 細長、鏤空的物件要求「chunky solid forms」「thick」之類的描述，3D 重建才不會斷。
- 不要在物件上要求文字或標誌；需要的話之後在 Blender 貼上去。
- `image_slab`：描述畫面內容本身，suffix 要求正面、滿版、無邊框、無透視；螢幕畫面另外寫明底色。

**輸出**：`scene.json`。**通過條件**：JSON 可解析；每個物件都有 `id` 與 `prompt`；`image_slab` 都有 `aspect` 與 `suffix`。

## 5. 步驟 2：迭代生成 2D 圖（核心步驟）

啟動 ComfyUI（一次即可，整個步驟 2 期間保持開啟）：

```bash
wsl -e bash -lc 'bash $P/pipeline/comfy_start.sh'      # 印出 COMFY_READY 才算成功
```

對**每個物件**執行下面的迴圈，一次只生成一張：

```
loop:
  生成一張   →  01_generate.py --next <id> [--prompt "..."] [--review-prev "..."]
  Read 這張 cand_n.png
  依「審核清單」寫下評語
  若有缺點   →  針對缺點改寫 prompt，下一輪帶 --prompt 與 --review-prev
  若已夠好   →  不改 prompt，下一輪只帶 --review-prev（換種子多拿一張備選）
  結束條件   →  已有 ≥ 4 張，且其中至少 1 張通過審核清單
  上限       →  8 張仍沒有合格的，選最接近的一張並在總結中註明
```

指令：

```bash
# 第 1 張：用 scene.json 的 prompt
wsl -e bash -lc 'python3 $P/pipeline/01_generate.py $P --next office_chair'

# 之後每一張：先寫對上一張的評語；要改 prompt 才加 --prompt
wsl -e bash -lc 'python3 $P/pipeline/01_generate.py $P --next office_chair \
  --review-prev "good shape but the castors are cut off at the bottom edge" \
  --prompt "A modern black ergonomic office task chair ... The chair is small in the frame with a wide empty margin below the castors."'
```

- `--prompt` 省略時沿用上一張的 prompt（只換種子）。
- 每次呼叫都會把 prompt、種子、耗時寫進 `work/objects/<id>/prompts.json`；`--review-prev` 的評語記在上一筆，形成完整的修改理由紀錄。
- prompt 定稿後把它寫回 `scene.json`，讓 `scene.json` 與實際使用的 prompt 一致。
- 物件很多、prompt 已經寫得穩的時候，可以先用批次模式一次生成（`01_generate.py $P [id ...]`，同一個 prompt 換種子 4 張），
  看完 `sheet.png` 後只對不合格的物件走迭代。

**審核清單**（每張都要逐項看）

| 項目 | 不合格的樣子 |
|---|---|
| 單一物件 | 畫面裡多了別的東西、多出一個同款物件、有人 |
| 完整 | 被畫面邊緣裁掉、貼著邊緣沒有留白 |
| 符合描述 | 種類、顏色、形狀、部件數量、**姿態**與 prompt 或文字描述不符 |
| 風格一致 | 和同一個空間的其他物件不是同一個世界的東西（太卡通、太復古、顏色體系不對） |
| 背景乾淨 | 不是純白、有地面、有明顯投影 |
| 適合 3D | 純正面看不出厚度（`image_slab` 除外）、極端透視、細長部件糊在一起、有透明部件 |
| 文字（`image_slab`） | 出現文字描述沒有寫的字、亂碼 |

**改寫 prompt 的原則**

- 一次只針對看到的缺點改，不要整段重寫；保留已經有效的描述。
- 缺什麼就明講：「all five castors fully inside the frame」「exactly four legs」「forks raised to 1.5 metres」。
- 模型沒照做時換說法或加強調，不要只是重複同一句。
- 已經合格就不要再改 prompt；之後的張數只是換種子取得備選。

**輸出**：每個物件 `cand_0.png … cand_k.png`（k ≥ 3）與 `prompts.json`。全部物件完成後：

```bash
wsl -e bash -lc 'bash $P/pipeline/comfy_stop.sh'       # 印出 COMFY_STOPPED 才能進下一步
```

## 6. 步驟 3：選圖與去背

產生對照表並逐一 Read：

```bash
wsl -e bash -lc 'source ~/miniconda3/etc/profile.d/conda.sh; conda activate trellis2; python $P/pipeline/02_select_matte.py $P sheet'
# → work/objects/<id>/sheet.png（左起：cand_0、cand_1、…）
```

寫 `work/selection.json`，每個物件一筆：

```json
{
  "office_chair":  {"pick": 0, "why": "五個輪子與扶手都清楚；cand 2 椅背變形"},
  "poster_safety": {"pick": 1, "why": "正面、滿版、沒有多餘文字"}
}
```

去背並檢查：

```bash
wsl -e bash -lc 'source ~/miniconda3/etc/profile.d/conda.sh; conda activate trellis2; python $P/pipeline/02_select_matte.py $P matte'
# → rgba.png（透明背景）、review.png（左：原圖，右：洋紅底去背結果）
```

Read 每個 `review.png`。**通過條件**：物件完整、沒有殘留陰影或白邊大塊、細長部件（桿、腳、葉片）沒被吃掉、白色物件沒有被當成背景挖掉。
不合格就改選別張，對該物件重跑 `matte <id>`。`image_slab` 不需要去背結果（步驟 5 直接用候選圖），它的 `review.png` 可以不看。

## 7. 步驟 4：生成、檢視 3D 模型並減面

```bash
# 背景執行；失敗會自動重啟並跳過已完成的物件
wsl -e bash -lc 'bash $P/pipeline/trellis_loop.sh $P 8 > $P/work/trellis.log 2>&1'
```

監看 `work/trellis.log`：`[done] <id> in Ns`、`[FAIL] <id>`、`[all done]`、`[gave up after 8 attempts]`。

### 減面

場景裡有幾十種模型、上百個複本，不減面會重到無法操作。減面分兩層：

1. **TRELLIS 輸出時**：GLB 會先減到 `trellis.faces`（預設 30000）才烘焙貼圖，這一層品質最好，優先在這裡設對。

   | 物件 | `trellis.faces` |
   |---|---|
   | 小道具（小於 0.5 m：托盤、顯微鏡、滅火器） | 8000–15000 |
   | 一般家具與設備（椅子、推車、機台） | 20000–40000 |
   | 大型或主視覺物件（雕塑、樹、車輛） | 50000–80000 |

2. **匯入 Blender 後**：印出每個模型的面數。同款複本很多的物件用「面數 × 複本數」衡量，超過約 50 萬就再減：
   加 Decimate 修改器（Collapse，ratio 不低於約 0.3）並套用；複本共用網格，只要做一次。減完用 `look` 確認輪廓沒有破、貼圖沒有錯位。
   要減更多就改 `trellis.faces` 重跑該物件，不要把 ratio 壓得更低。

表中的數字是起點，不是硬性規定；以「近看輪廓不出現明顯折角」為準往下調。組裝完成後在總結裡列出場景總面數。

### 匯入與檢視

完成後用 MCP 把每個 `model.glb` 匯入 Blender 檢視（這些物件之後直接留在場景裡用）：

1. `execute_blender_code`：`bpy.ops.import_scene.gltf(filepath=...)`，合併成單一網格、套用變換、原點移到底部中心，物件命名為 `<id>`，
   放進 `Models_TRELLIS` 集合。先放在遠離場景的暫存位置排成一列即可。
   **glTF 匯入的物件是四元數旋轉**：先設 `obj.rotation_mode = 'XYZ'`，否則之後設 `rotation_euler` 不會有任何效果（複本也會繼承）。
2. `look`：`mode="angles"`、`target=["<id>"]`、`shading="material"`，至少看正面與背面斜角兩個方向。
3. 逐一對照選定的 2D 圖判斷。

| 看到的問題 | 處理方式 |
|---|---|
| 同一物件反覆 `[FAIL]` | 看 `trellis.log` 的錯誤；多半是去背圖有問題（幾乎全透明、物件太小），回步驟 3 換一張 |
| 文字或字母變成亂碼 | 改成 `"mode": "image_slab"`（刪掉匯入的模型）；需要一張正面 2D 圖，沒有就回步驟 2 用正面視角重新生成 |
| 透明部件變成不透明白色、容器裡的東西消失 | 依步驟 0 的表改做法：玻璃在 Blender 建，內容物單獨生成；舊物件設 `status: "rejected"` 並寫 `note` |
| 形狀明顯錯誤、破面、多出雜物 | 刪掉該物件的 `model.glb`，在 `trellis` 設不同的 `"seed"` 重跑；仍不行就回步驟 2 換一張 2D 圖 |
| 貼圖偏色（鏡面金屬、純白物件） | 形狀留用，在 Blender 換材質或在 Base Color 前插一個色彩校正 |
| 形狀正確但不夠精細（細節糊掉、薄件變厚、玩家會近看的主要物件） | 在 `trellis` 設 `"pipeline_type": "1024_cascade"` 重跑，必要時一併提高 `faces`。12 GB VRAM 上實心方塊與枝葉類曾因此 CUDA 錯誤；反覆 `[FAIL]` 就改回 512 |
| 貼圖略糊但形狀正確，且是遠處小道具 | 可接受，在總結中註明 |

重做單一物件：刪除 `work/objects/<id>/model.glb` 與 Blender 裡的舊物件後重跑 `trellis_loop.sh`，再重新匯入。每個物件最多重做 2 次，之後就接受並註明。

**輸出**：每個非 `image_slab` 物件的 `work/objects/<id>/model.glb`，且都已匯入 Blender、看過、面數在預算內。

## 8. 步驟 5：用 Blender MCP 組裝場景

這一步沒有建置腳本。場景由你透過 Blender MCP 在使用者開著的 Blender 裡直接建出來：`execute_blender_code` 執行 `bpy` 程式碼，
`look` 看結果，`get_scene_info` 查物件的位置與尺寸。**`scene.blend` 本身就是成果，也是唯一的狀態**；要改場景就改 Blender 裡的東西，
不要把建置邏輯寫成磁碟上的 `.py` 檔。

**工作方式**

- 小步進行：每次 `execute_blender_code` 只做一件事（建一個空間的外殼、擺一組物件、調一組燈），印出關鍵數值，接著 `look` 確認，再做下一件。
- 不要跨呼叫依賴 Python 變數；每段程式碼都從 `bpy.data.objects["名稱"]` 重新取得物件。
- **重複用的小函式存在 .blend 裡的文字區塊**：建一個 Text datablock（慣例：`h` 放建模與擺位函式，`hm` 放材質函式），
  每次呼叫開頭 `exec(bpy.data.texts["h"].as_string())`。它跟著 `scene.blend` 存檔，不是磁碟上的建置腳本。
  接手既有場景時先看這兩個文字區塊在不在、裡面有什麼，再決定沿用或重寫。
- 物件命名要讓之後的自己（或下一個工作階段）看得懂：模型用 `scene.json` 的 `<id>`、複本用 `<id>_2`、建築用 `<空間>_Floor`、`<空間>_Wall_N` 這類名稱、
  燈用 `L_` 開頭、相機用 `Cam_<空間>`。集合：`Models_TRELLIS`、`Lights_Camera`，建築與道具依空間分（`Room_A1`、`Room_A2`…）。
- **依名稱前綴刪除物件時要小心**：`Cam_A1` 的前綴也會比對到 `Cam_A10`–`Cam_A17`。用完整名稱，或前綴帶上分隔符號（`A1_`）。
- 依 MCP 的規則寫程式碼：著色器節點用 `type` 找（`n.type == "BSDF_PRINCIPLED"`），不要用名稱；列舉值先讀再設。
- 每完成一個空間就存檔：`bpy.ops.wm.save_as_mainfile(filepath=r"$PW\scene.blend")`（第一次）或 `bpy.ops.wm.save_mainfile()`。
- `look` 的 `max_size` 平常用 800–1200 即可，只有要看細節時才放大。

### 5.1 配置與座標

- 世界座標：**+Y 是北、+X 是東、Z 向上**，地面 z = 0，單位公尺。文字描述若以「入口在南牆、面朝北」寫左右，西 = 左 = -X，東 = 右 = +X。
- 每個空間建一個 Empty（`Room_<編號>`）放在該空間的原點（建議：南側入口的地面中點），空間內的東西都設為它的子物件，
  用空間自己的座標建模。空間的實際朝向用這個 Empty 的 Z 旋轉決定，這樣文字裡的「北牆」「西牆」可以直接照寫。
- 依連接關係從入口空間開始往外排：每放一個空間，就讓它的門洞中心與相鄰空間的門洞中心重合，牆厚留 0.2–0.3 m。
  全部排完後從正上方 `look` 一次，確認沒有空間重疊、每個出入口都接得到東西。
- 朝向慣例：物件正面朝 -Y 時為 yaw 0。掛在西牆（面朝東）的東西 yaw +90°，東牆 -90°，南牆 180°，北牆 0°。

### 5.2 建築

地面、牆、天花、門窗、樓梯、櫃台、層架、花台、遠景都在這裡建。用 `bmesh` 或 `from_pydata` 直接產生網格。

- **照文字的分割方式建，不要用幾個平面交差。** 地磚一塊一塊建（板與板之間留縫）或至少有縫線幾何；牆面有分割板、踢腳、腰線；
  天花有輕鋼架格、燈槽、出風口、灑水頭；門有門框、門把、門禁面板；窗有窗框與分格。
- 門洞要真的開在牆上，相鄰空間之間走得過去。玻璃牆、玻璃門用玻璃材質，從這個空間要看得到隔壁。
- 硬邊加 Bevel 修改器（1 段、寬 1–3 cm）；沒有倒角的邊吃不到高光，會顯得假。
- 玩家到不了、也看不到的地方（建築背面、屋頂上方）用簡單的方盒封住即可，但不要留下會露出天空或虛空的破口。
- 戶外空間要有遠景：周圍建築的量體、樹列、圍牆、道路，避免地平線上空無一物。
- 標示文字用 Blender 文字物件（內容照文字描述一字不差），需要中文時指定有中文字形的字型檔。

每建完一個空間就 `look`：一張從南側入口往北看（第一人稱高度），一張從斜上方看，確認尺寸、門洞位置與文字描述一致。

### 5.3 匯入模型並擺位

模型在步驟 4 檢視時已匯入並減面。對每個物件：

1. **整理**：合併成單一網格、套用變換、原點在底部中心、`rotation_mode = 'XYZ'`；模型的正面若不是朝 -Y，先在網格資料上轉正。
2. **定大小**：依 `height_m`（或物件的真實尺寸）等比縮放，再跟旁邊的門（2.1 m）、桌面（0.75 m）比一下是否合理。
3. **定位置**：照文字描述的位置（靠哪面牆、離牆多遠）；文字沒寫的自己決定，但要合理：椅子在桌邊、垃圾桶在牆角、推車靠牆。
4. **定朝向**：對齊所在的牆或家具，不要全部朝同一個方向。
5. **重複物件**：`obj.copy()` 產生共用網格的複本，各自擺位，不要重新匯入；同款複本各自轉一點角度、位置稍微錯開，避免看起來像陣列。
6. `look` 確認位置、大小；再用 `mode="angles"` 從側面或上方看一次，確認沒有懸空、沒有插進地面或牆裡。
   `get_scene_info` 的 `ground` 欄位可以直接列出誰浮著。

**`image_slab` 物件**（海報、螢幕畫面、照片、畫作）：建一塊薄板，正面貼選定的候選圖（`cand_<pick>.png`，不是去背圖），
加上畫框或螢幕外殼的幾何。螢幕與燈箱把貼圖同時接到 Emission。

**常見修正**

| 問題 | 做法 |
|---|---|
| 生成的貼圖在場景裡太亮或太暗 | 在 Base Color 前插一個 Multiply（0.3–0.6 壓暗，1.5–2 提亮） |
| 白色物件貼圖偏灰或偏色，和白色建築不搭 | 色彩校正拉回，或直接換成和建築同一套的材質 |
| 模型上的螢幕、面板是糊的 | 另外生成一張 `image_slab` 畫面，做成貼在模型正面前方幾毫米的發光平面，設為模型的子物件 |
| 同款物件看起來一模一樣 | 複本各自轉一點角度；海報類換圖 |

### 5.4 填滿：不留大面積空平面

這是硬性要求：**玩家看得到的地方不能有大面積的空平面**。建築與主要物件放完後，逐一從每個空間的第一人稱相機看（每個空間至少兩個方向），
凡是還看得出一整片空的地面、牆面、天花（大約 3 m × 3 m 以上沒有任何東西打斷），就加東西，直到沒有為止。

| 表面 | 可以加的東西 |
|---|---|
| 地面 | 鋪面分縫、嵌入式導引線與圖樣、地毯、防滑墊、排水溝蓋、檢修蓋；盆栽、長椅、垃圾桶、立牌、推車、紙箱、落葉或碎屑（依空間狀態） |
| 牆面 | 分割板縫、踢腳與腰線、扶手、標示牌、海報與畫作、螢幕、佈告欄、滅火器箱、開關與插座、門禁面板、管線、通風格柵、壁燈、掛鐘 |
| 天花 | 輕鋼架格、燈具與燈槽、出風口、灑水頭、煙霧偵測器、監視器、指示牌、吊掛管線與線槽、造型吊飾 |
| 桌面與層架 | 螢幕、鍵盤、文件、杯子、檔案盒、儀器、樣本盒、小盆栽 |

- 加的東西要符合該空間的用途與狀態（整潔的企業大廳不撒垃圾，崩塌的通道才有碎石），並與文字描述的視覺體系一致。
- 小東西優先用 Blender 直接建（方盒、圓柱加倒角與材質就夠），需要辨識度的才回步驟 1 生成新物件。
- 散佈物做 3–5 個原型網格，其餘用共用網格的複本，隨機位置、旋轉、大小；避開已放好的物件，全場景不超過 500 個。
- 不要用貼圖假裝有東西：遠處可以，玩家走得到的地方要有實際幾何。

**通過條件**：每個空間的每個檢查視角都沒有大片空面；加上去的東西沒有擋住出入口與文字描述的動線。

### 5.5 表面材質

**材質**用程序化節點直接做，座標用世界空間位置（Geometry → Position），紋理尺度就是真實的公尺，建築不需要 UV。
牆面的磚縫、板縫這類圖樣用向量 `(x + y, z)` 就能同時適用兩個方向的牆。

- 依文字描述的材質與顏色做；同一種材質全場景共用一個材質，不要每個空間各做一份。
- 混凝土 / 地面：大尺度噪波做明暗，再乘一層更大尺度的噪波當髒污（髒的地方粗糙度高），細噪波接 Bump。乾淨的空間對比要很小。
- 金屬、塗裝、玻璃：Principled BSDF 調 Metallic / Roughness / Transmission 即可。
- 程序化做不出來的質感才用 Poly Haven（CC0）：`python pipeline/polyhaven.py search|size|get`（下載到 `work/textures/<id>/`），
  下載前先告訴使用者要下載什麼、來源、大小，預設 1k；在 Blender 裡用 Box 投影的 Image Texture 接上。只用 Poly Haven，其他來源授權不一。

| 看到的問題 | 調整 |
|---|---|
| 紋理顆粒大得像巨石，或小到變成雜訊 | 改 Mapping 的 Scale（對照場景裡物件的真實大小） |
| 大片白色表面過曝成一片 | 基色降到 0.7–0.8，靠燈光把它打亮 |
| 地面反光太強 | 提高粗糙度 |

### 5.6 燈光

確定 ComfyUI 與 TRELLIS 都已停止。引擎用 EEVEE（開 raytracing），色彩管理 AgX。

- 照文字描述的「光線與色彩」做：光源位置、色溫、明暗分布。每個看得到的燈具（燈槽、吊燈、螢幕、指示燈）用 Emission，並配一盞對應的燈。
- 一個空間一個空間做，每加一組燈就 `look`（`rendered`）。主光要有方向；補光最後才加，而且要弱。
- 戶外用 Sky 貼圖或 Sun 加天空色的 World；室內靠窗的空間要讓日光真的照進來。
- 霧與光暈只在文字有寫時才加：霧用 Volume Scatter 方盒（密度從 0.001 起），光暈用合成器的 Glare 節點（`look` 的 `rendered` 看不到合成器效果，要真的渲染一次）。
- 燈很多時 EEVEE 會回報 shadow buffer 已滿：小的點綴燈關掉陰影，或調 `scene.eevee.shadow_pool_size`。

### 5.7 相機、渲染、存檔

- 每個空間建一台第一人稱相機 `Cam_<空間>`：高度與水平視角取 `scene.json` 的 `camera`（預設 1.6 m、90°），鏡頭水平不傾斜，
  站在南側入口朝北看。大空間再加 `Cam_<空間>b`（從北端回望）、`c`、`d`（側面）。
- `OverviewCamera` 放在場景斜上方、看得到全區的位置，用來檢查配置與懸空。

```python
# 透過 execute_blender_code 執行；每台相機一張
scene.camera = bpy.data.objects["Cam_A1"]; scene.render.filepath = r"$PW\work\renders\view_A1.png"; bpy.ops.render.render(write_still=True)
# … 其餘相機同理；OverviewCamera → view_overview.png
bpy.ops.file.pack_all(); bpy.ops.wm.save_mainfile()
```

全部渲染完後拼成一張總表並 Read：

```bash
wsl -e bash -lc 'source ~/miniconda3/etc/profile.d/conda.sh; conda activate trellis2; python $P/pipeline/04_contact_sheet.py $P'
# → work/renders/contact_sheet.png
```

**對照文字描述驗收**（沒有參考圖，文字就是標準）：一個空間一個空間，把該小節重讀一次，對著渲染圖逐項核對，回到 Blender 修正，最多 3 輪：

- 尺寸與格局、每個出入口與它通往的空間；
- 文字列出的每一個固定物件是否存在、位置是否正確；
- 標示文字的內容是否一字不差；
- 光源、色溫、明暗；
- 狀態細節（破損、污漬、遺留物）；
- 步驟 5.4 的填滿要求。

**通過條件**：上列各項逐一核對過，對不上的都已修正或記進總結；沒有物件懸空或穿插；
`scene.blend` 已存檔且貼圖已打包（沒有未打包的外部圖檔）。

## 9. 步驟 6：總結回報

交付時說明：

- 空間表：建了哪些空間、尺寸、相機名稱。
- 物件表：每個物件的做法（TRELLIS 512 / 1024_cascade / image_slab / 在 Blender 建模）、生成了幾張 2D 圖、prompt 改了幾次、面數、最終品質。
- 自己補上的東西：文字沒寫、為了填滿或讓空間可信而加的物件。
- 表面材質：各表面是程序化節點還是 Poly Haven 素材（附 asset id）。
- 不合格或妥協的項目與原因。
- 與文字描述的已知差異。
- 場景總面數、`scene.blend` 檔案大小。
- 各階段耗時（可從 `work/generation_log.jsonl` 和 `work/trellis.log` 取得）。
- 確認 ComfyUI 與 TRELLIS 都已停止。

只回報實際看過、驗證過的結果；沒檢查的項目要明說沒檢查。

## 10. 檔案對照

```
<場景文字描述>.md               輸入：空間的文字描述（使用者提供）
scene.json                      物件定義（你撰寫與修改）
pipeline/01_generate.py         FLUX.2 klein 4B 文字生圖（--next 迭代模式 / 批次模式）
pipeline/comfy_t2i.py           ComfyUI API 客戶端（被 01 呼叫）
pipeline/comfy_start.sh|stop.sh 啟動 / 停止 ComfyUI
pipeline/02_select_matte.py     對照表（sheet）與去背（matte）
pipeline/03_trellis_batch.py    TRELLIS.2 批次，輸出已減面的 GLB（被 loop 呼叫）
pipeline/trellis_loop.sh        TRELLIS 自動重啟迴圈
pipeline/04_contact_sheet.py    把所有 view_*.png 拼成一張總表
pipeline/polyhaven.py           搜尋 / 下載 Poly Haven CC0 貼圖
work/objects/<id>/              cand_*.png、prompts.json、sheet.png、rgba.png、review.png、model.glb
work/selection.json             選圖結果與理由（你撰寫）
work/textures/<asset id>/       下載的 Poly Haven 貼圖
work/renders/                   各相機的渲染圖與總表
scene.blend                     場景本身：由你透過 Blender MCP 建立，沒有對應的建置腳本
```
