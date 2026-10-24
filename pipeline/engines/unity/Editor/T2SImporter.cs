// Unity importer: export/ -> Assets/T2S (materials, prefabs with LODGroup + colliders, scene, review shots).
//
// Copy this Editor folder into the Unity project (e.g. Assets/T2S/Editor/). Works with URP, HDRP and the
// Built-in pipeline: pipeline-specific types (volumes, decals) are reached by reflection, so the file compiles
// whichever pipeline is installed.
//
//   in the editor:  Tools > Text2Scene > Import Assets + Build Level   (asks for the export folder once)
//   batch mode:     Unity.exe -batchmode -projectPath <project> -executeMethod T2S.T2SImporter.CommandLine
//                       -t2sExport <project_dir>/export [-t2sAssetsOnly] [-t2sLevelOnly] [-t2sShots] [-t2sBake]
//                       -quit -logFile <log>
//
// Axes: level.json is metres, right-handed Z-up (+Y north). Unity is left-handed Y-up; swapping Y and Z flips
// the handedness: unity = (x, z, y), yaw_unity = -yaw. The FBX files arrive with the same mapping (model importer
// bakeAxisConversion), and SM_T2S_Axes checks it on every import.
// Re-running is safe: files are copied only when changed, the scene is rebuilt from level.json.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using Object = UnityEngine.Object;

namespace T2S
{
    public static class T2SImporter
    {
        const string Root = "Assets/T2S";
        const string Imported = Root + "/Imported";
        static string exportDir;
        static Dictionary<string, Dictionary<string, object>> assets;
        static readonly Dictionary<string, Material> materials = new Dictionary<string, Material>();
        static readonly Dictionary<string, GameObject> prefabs = new Dictionary<string, GameObject>();
        enum Pipeline { Builtin, URP, HDRP }
        static Pipeline pipeline;

        // ------------------------------------------------------------------ entry points
        [MenuItem("Tools/Text2Scene/Import Assets + Build Level")]
        static void MenuAll() { if (PickExport()) Run(true, true, false, false); }

        [MenuItem("Tools/Text2Scene/Import Assets Only")]
        static void MenuAssets() { if (PickExport()) Run(true, false, false, false); }

        [MenuItem("Tools/Text2Scene/Build Level Only")]
        static void MenuLevel() { if (PickExport()) Run(false, true, false, false); }

        [MenuItem("Tools/Text2Scene/Render Review Shots")]
        static void MenuShots() { if (PickExport()) Run(false, true, true, false); }

        static bool PickExport()
        {
            var last = EditorPrefs.GetString("T2S.export", "");
            var dir = EditorUtility.OpenFolderPanel("text2scene export folder", last, "");
            if (string.IsNullOrEmpty(dir) || !File.Exists(Path.Combine(dir, "level.json"))) return false;
            EditorPrefs.SetString("T2S.export", dir);
            exportDir = dir;
            return true;
        }

        public static void CommandLine()
        {
            var args = Environment.GetCommandLineArgs();
            int i = Array.IndexOf(args, "-t2sExport");
            if (i < 0 || i + 1 >= args.Length) throw new ArgumentException("-t2sExport <dir> is required");
            exportDir = args[i + 1];
            bool assetsOnly = args.Contains("-t2sAssetsOnly"), levelOnly = args.Contains("-t2sLevelOnly");
            Run(!levelOnly, !assetsOnly, args.Contains("-t2sShots"), args.Contains("-t2sBake"));
        }

        static void Run(bool doAssets, bool doLevel, bool shots, bool bake)
        {
            var rp = GraphicsSettings.currentRenderPipeline;
            var rpName = rp == null ? "" : rp.GetType().Name;
            pipeline = rpName.Contains("Universal") ? Pipeline.URP : rpName.Contains("HD") ? Pipeline.HDRP : Pipeline.Builtin;
            Log($"pipeline {pipeline}, export {exportDir}");
            GraphicsSettings.lightsUseLinearIntensity = true;
            GraphicsSettings.lightsUseColorTemperature = true;
            materials.Clear();
            prefabs.Clear();
            LoadAssets();
            if (doAssets) ImportAssets();
            else LoadExisting();
            if (doLevel) BuildLevel(shots, bake);
            AssetDatabase.SaveAssets();
            Log("done");
        }

        static void Log(string m) => Debug.Log("[t2s] " + m);
        static void Warn(string m) => Debug.LogWarning("[t2s] " + m);

        // ------------------------------------------------------------------ conversion
        static Vector3 Pos(object o) { var p = Floats(o); return new Vector3(p[0], p[2], p[1]); }
        static Vector3 Dir(object o) { var p = Floats(o); return new Vector3(p[0], p[2], p[1]).normalized; }
        static Quaternion Yaw(double yaw) => Quaternion.Euler(0f, (float)-yaw, 0f);
        static Quaternion Look(object dir, object up = null)
        {
            var f = Dir(dir);
            var u = up != null ? Dir(up) : (Mathf.Abs(Vector3.Dot(f, Vector3.up)) > 0.99f ? Vector3.forward : Vector3.up);
            return Quaternion.LookRotation(f, u);
        }

        // ------------------------------------------------------------------ files
        static void LoadAssets()
        {
            assets = new Dictionary<string, Dictionary<string, object>>();
            foreach (var f in Directory.GetFiles(exportDir, "asset.json", SearchOption.AllDirectories))
            {
                var a = (Dictionary<string, object>)Json.Parse(File.ReadAllText(f));
                a["_dir"] = Path.GetDirectoryName(f);
                var name = a.ContainsKey("name") ? (string)a["name"] : (string)a["id"];
                assets[name] = a;
            }
        }

        static string UnityPath(Dictionary<string, object> a, string file)
        {
            var rel = Path.GetRelativePath(exportDir, Path.Combine((string)a["_dir"], file)).Replace('\\', '/');
            return $"{Imported}/{rel}";
        }

        static void CopyExport()
        {
            int copied = 0;
            AssetDatabase.StartAssetEditing();
            try
            {
                foreach (var src in Directory.GetFiles(exportDir, "*", SearchOption.AllDirectories))
                {
                    var ext = Path.GetExtension(src).ToLowerInvariant();
                    if (ext != ".png" && ext != ".fbx") continue;
                    var rel = Path.GetRelativePath(exportDir, src);
                    if (rel.StartsWith("shots")) continue;
                    var dst = Path.Combine(Imported, rel);
                    var fi = new FileInfo(src);
                    var di = new FileInfo(dst);
                    if (di.Exists && di.Length == fi.Length && di.LastWriteTimeUtc >= fi.LastWriteTimeUtc) continue;
                    Directory.CreateDirectory(Path.GetDirectoryName(dst));
                    File.Copy(src, dst, true);
                    copied++;
                }
            }
            finally { AssetDatabase.StopAssetEditing(); }
            AssetDatabase.Refresh();
            Log($"copied {copied} changed files into {Imported}");
        }

        // ------------------------------------------------------------------ assets
        static void ImportAssets()
        {
            CopyExport();
            Calibrate();
            Directory.CreateDirectory(Root + "/Materials");
            Directory.CreateDirectory(Root + "/Prefabs");
            foreach (var a in assets.Values.Where(a => Str(a, "kind") == "material")) SurfaceMaterial(a);
            foreach (var a in assets.Values.Where(a => Str(a, "kind") == "decal")) DecalMaterial(a);
            foreach (var kv in assets.Where(kv => Str(kv.Value, "kind") == "prop" || Str(kv.Value, "kind") == "kit"))
                Prefab(kv.Key, kv.Value);
            AssetDatabase.SaveAssets();
        }

        static void LoadExisting()
        {
            foreach (var g in AssetDatabase.FindAssets("t:Material", new[] { Root + "/Materials" }))
            {
                var m = AssetDatabase.LoadAssetAtPath<Material>(AssetDatabase.GUIDToAssetPath(g));
                materials[m.name.StartsWith("MI_") ? m.name.Substring(3) : m.name] = m;
            }
            foreach (var g in AssetDatabase.FindAssets("t:Prefab", new[] { Root + "/Prefabs" }))
            {
                var p = AssetDatabase.LoadAssetAtPath<GameObject>(AssetDatabase.GUIDToAssetPath(g));
                prefabs[p.name] = p;
            }
        }

        static void Calibrate()
        {
            if (!assets.TryGetValue("SM_T2S_Axes", out var a)) return;
            var path = UnityPath(a, Str(Lods(a)[0], "file"));
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            if (model == null) { Warn("axis calibration mesh missing"); return; }
            var b = model.GetComponentsInChildren<MeshFilter>().Select(mf =>
            {
                var bb = mf.sharedMesh.bounds;
                var m = mf.transform.localToWorldMatrix;
                var c1 = m.MultiplyPoint3x4(bb.min); var c2 = m.MultiplyPoint3x4(bb.max);
                var r = new Bounds(c1, Vector3.zero); r.Encapsulate(c2); return r;
            }).First();
            var lo = Pos(a["bounds_min"]); var hi = Pos(a["bounds_max"]);
            var want = new Bounds(lo, Vector3.zero); want.Encapsulate(hi);
            bool ok = (b.min - want.min).magnitude < 0.03f && (b.max - want.max).magnitude < 0.03f;
            var msg = $"axis calibration {(ok ? "OK" : "MISMATCH")}: bounds {b.min}..{b.max}, expected {want.min}..{want.max}";
            if (ok) Log(msg); else throw new Exception(msg + " (fix Pos()/Yaw() or the FBX import settings)");
        }

        static Texture2D Tex(Dictionary<string, object> a, string file)
        {
            return file == null ? null : AssetDatabase.LoadAssetAtPath<Texture2D>(UnityPath(a, file));
        }

        static Texture2D shared(string key)
        {
            var a = assets["shared"];
            if (key == "macro" || key == "detail_n") return Tex(a, Str(a, key));
            return Tex(a, Str((Dictionary<string, object>)a["default"], key));
        }

        static Texture2D neutralDetail;
        static Texture2D NeutralDetailAlbedo()
        {
            // mid grey (sRGB 128) is neutral for the Lit shaders' x2 detail albedo
            var path = Root + "/Materials/T_DetailAlbedoNeutral.asset";
            neutralDetail = AssetDatabase.LoadAssetAtPath<Texture2D>(path);
            if (neutralDetail != null) return neutralDetail;
            neutralDetail = new Texture2D(4, 4, TextureFormat.RGBA32, false, false);
            neutralDetail.SetPixels(Enumerable.Repeat(new Color(0.5f, 0.5f, 0.5f, 1f), 16).ToArray());
            neutralDetail.Apply();
            AssetDatabase.CreateAsset(neutralDetail, path);
            return neutralDetail;
        }

        static Shader LitShader()
        {
            switch (pipeline)
            {
                case Pipeline.URP: return Shader.Find("Universal Render Pipeline/Lit");
                case Pipeline.HDRP: return Shader.Find("HDRP/Lit");
                default: return Shader.Find("Standard");
            }
        }

        static Material NewMaterial(string name, Shader shader)
        {
            var path = $"{Root}/Materials/MI_{name}.mat";
            var m = AssetDatabase.LoadAssetAtPath<Material>(path);
            if (m == null) { m = new Material(shader) { name = "MI_" + name }; AssetDatabase.CreateAsset(m, path); }
            else m.shader = shader;
            m.enableInstancing = true;
            return m;
        }

        /// <summary>Lit material from canonical maps; MASK = R metal, G AO, B detail mask, A smoothness.</summary>
        static void SetupLit(Material m, Texture2D bc, Texture2D n, Texture2D mask, Color tint, float tiling,
                             Texture2D detailN = null, float detail = 0f, float emissive = 0f, float smoothScale = 1f)
        {
            var scale = new Vector2(tiling, tiling);
            switch (pipeline)
            {
                case Pipeline.URP:
                    m.SetTexture("_BaseMap", bc); m.SetColor("_BaseColor", tint); m.SetTextureScale("_BaseMap", scale);
                    m.SetTexture("_BumpMap", n); m.EnableKeyword("_NORMALMAP");
                    m.SetTexture("_MetallicGlossMap", mask); m.EnableKeyword("_METALLICSPECGLOSSMAP");
                    m.SetFloat("_Smoothness", smoothScale); m.SetFloat("_SmoothnessTextureChannel", 0);
                    m.SetTexture("_OcclusionMap", mask); m.EnableKeyword("_OCCLUSIONMAP");
                    if (detailN != null && detail > 0)
                    {
                        m.SetTexture("_DetailAlbedoMap", NeutralDetailAlbedo()); m.SetTexture("_DetailNormalMap", detailN);
                        m.SetFloat("_DetailNormalMapScale", detail); m.SetTextureScale("_DetailAlbedoMap", scale * 4);
                        m.EnableKeyword("_DETAIL_MULX2");
                    }
                    break;
                case Pipeline.HDRP:
                    m.SetTexture("_BaseColorMap", bc); m.SetColor("_BaseColor", tint); m.SetTextureScale("_BaseColorMap", scale);
                    m.SetTexture("_NormalMap", n); m.EnableKeyword("_NORMALMAP");
                    m.SetTexture("_MaskMap", mask); m.EnableKeyword("_MASKMAP");
                    m.SetFloat("_MetallicRemapMax", 1); m.SetFloat("_SmoothnessRemapMax", smoothScale); m.SetFloat("_AORemapMax", 1);
                    break;
                default:
                    m.SetTexture("_MainTex", bc); m.SetColor("_Color", tint); m.SetTextureScale("_MainTex", scale);
                    m.SetTexture("_BumpMap", n); m.EnableKeyword("_NORMALMAP");
                    m.SetTexture("_MetallicGlossMap", mask); m.EnableKeyword("_METALLICGLOSSMAP");
                    m.SetFloat("_GlossMapScale", smoothScale); m.SetFloat("_SmoothnessTextureChannel", 0);
                    m.SetTexture("_OcclusionMap", mask);
                    if (detailN != null && detail > 0)
                    {
                        m.SetTexture("_DetailAlbedoMap", NeutralDetailAlbedo()); m.SetTexture("_DetailNormalMap", detailN);
                        m.SetFloat("_DetailNormalMapScale", detail); m.SetTextureScale("_DetailAlbedoMap", scale * 4);
                        m.EnableKeyword("_DETAIL_MULX2");
                    }
                    break;
            }
            if (emissive > 0)
            {
                m.EnableKeyword("_EMISSION");
                m.SetTexture(pipeline == Pipeline.HDRP ? "_EmissiveColorMap" : "_EmissionMap", bc);
                m.SetColor(pipeline == Pipeline.HDRP ? "_EmissiveColor" : "_EmissionColor", Color.white * emissive);
                m.globalIlluminationFlags = MaterialGlobalIlluminationFlags.BakedEmissive;
            }
            ValidateHDRP(m);
            EditorUtility.SetDirty(m);
        }

        static void SetupGlass(Material m, Color tint, float opacity, float roughness)
        {
            tint.a = opacity;
            switch (pipeline)
            {
                case Pipeline.URP:
                    m.SetColor("_BaseColor", tint); m.SetFloat("_Surface", 1); m.SetFloat("_Blend", 0);
                    m.SetFloat("_Smoothness", 1 - roughness); m.SetFloat("_Cull", 0);
                    m.SetFloat("_SrcBlend", (float)BlendMode.SrcAlpha); m.SetFloat("_DstBlend", (float)BlendMode.OneMinusSrcAlpha);
                    m.SetFloat("_ZWrite", 0); m.EnableKeyword("_SURFACE_TYPE_TRANSPARENT");
                    m.renderQueue = (int)RenderQueue.Transparent;
                    break;
                case Pipeline.HDRP:
                    m.SetColor("_BaseColor", tint); m.SetFloat("_SurfaceType", 1); m.SetFloat("_BlendMode", 0);
                    m.SetFloat("_Smoothness", 1 - roughness); m.SetFloat("_DoubleSidedEnable", 1);
                    break;
                default:
                    m.SetColor("_Color", tint); m.SetFloat("_Mode", 3); m.SetFloat("_Glossiness", 1 - roughness);
                    m.SetInt("_SrcBlend", (int)BlendMode.One); m.SetInt("_DstBlend", (int)BlendMode.OneMinusSrcAlpha);
                    m.SetInt("_ZWrite", 0); m.EnableKeyword("_ALPHAPREMULTIPLY_ON");
                    m.renderQueue = (int)RenderQueue.Transparent;
                    break;
            }
            ValidateHDRP(m);
            EditorUtility.SetDirty(m);
        }

        static void ValidateHDRP(Material m)
        {
            if (pipeline != Pipeline.HDRP) return;
            var t = Type.GetType("UnityEditor.Rendering.HighDefinition.HDShaderUtils, Unity.RenderPipelines.HighDefinition.Editor");
            t?.GetMethod("ResetMaterialKeywords", BindingFlags.Static | BindingFlags.Public)?.Invoke(null, new object[] { m });
        }

        static void SurfaceMaterial(Dictionary<string, object> a)
        {
            var id = Str(a, "id");
            var prm = Dict(a, "params");
            var m = NewMaterial(id, LitShader());
            if (Str(a, "shader") == "glass")
                SetupGlass(m, ColorOf(prm, "color", new Color(0.9f, 0.95f, 1f)), Num(prm, "opacity", 0.15f), Num(prm, "roughness", 0.03f));
            else
            {
                var t = Dict(a, "textures");
                SetupLit(m, Tex(a, Str(t, "bc")), Tex(a, Str(t, "n")), Tex(a, Str(t, "mask")), Color.white,
                         1f / Num(a, "tile_m", 2f), shared("detail_n"), Num(prm, "detail", 0.4f));
            }
            materials[id] = m;
            Log($"material {id}");
        }

        static void DecalMaterial(Dictionary<string, object> a)
        {
            var id = Str(a, "id");
            var t = Dict(a, "textures");
            Shader shader = pipeline == Pipeline.URP ? Shader.Find("Shader Graphs/Decal")
                          : pipeline == Pipeline.HDRP ? Shader.Find("HDRP/Decal") : null;
            if (shader == null) { Warn($"decal {id}: no decal shader in the {pipeline} pipeline, skipped"); return; }
            var m = NewMaterial(id, shader);
            if (pipeline == Pipeline.URP)
            {
                m.SetTexture("Base_Map", Tex(a, Str(t, "bc"))); m.SetTexture("Normal_Map", Tex(a, Str(t, "n")));
                m.SetFloat("Normal_Blend", 0.5f);
            }
            else
            {
                m.SetTexture("_BaseColorMap", Tex(a, Str(t, "bc"))); m.SetTexture("_NormalMap", Tex(a, Str(t, "n")));
                m.SetFloat("_DecalBlend", Num(Dict(a, "params"), "opacity", 1f));
                ValidateHDRP(m);
            }
            m.enableInstancing = true;
            EditorUtility.SetDirty(m);
            materials[id] = m;
        }

        static Material SlotMaterial(Dictionary<string, object> a, Dictionary<string, object> slot)
        {
            if (slot.ContainsKey("material"))
                return materials.TryGetValue(Str(slot, "material"), out var shared) ? shared : null;
            var name = Str(slot, "name");
            name = name.StartsWith("M_") ? name.Substring(2) : name;
            var t = slot.ContainsKey("textures") ? Dict(slot, "textures") : null;
            var prm = slot.ContainsKey("params") ? Dict(slot, "params") : new Dictionary<string, object>();
            var m = NewMaterial(name, LitShader());
            var tint = ColorOf(prm, "tint", Color.white);
            float smooth = prm.ContainsKey("roughness") ? (1f - Num(prm, "roughness", 0.5f)) / 0.5f : 1f;
            SetupLit(m, t != null ? Tex(a, Str(t, "bc")) : shared("bc"), t != null ? Tex(a, Str(t, "n")) : shared("n"),
                     t != null ? Tex(a, Str(t, "mask")) : shared("mask"), tint, 1f, emissive: Num(prm, "emissive", 0f),
                     smoothScale: smooth);
            materials[Str(slot, "name")] = m;
            return m;
        }

        static void Prefab(string name, Dictionary<string, object> a)
        {
            var lods = Lods(a);
            var slotMats = new Dictionary<string, Material>();
            foreach (Dictionary<string, object> slot in List(a, "slots"))
                slotMats[Str(slot, "name")] = SlotMaterial(a, slot);
            var root = new GameObject(name);
            var levels = new List<LOD>();
            for (int i = 0; i < lods.Count; i++)
            {
                var model = AssetDatabase.LoadAssetAtPath<GameObject>(UnityPath(a, Str(lods[i], "file")));
                if (model == null) { Warn($"{name}: LOD{i} model missing"); continue; }
                var inst = (GameObject)Object.Instantiate(model, root.transform);
                inst.name = i == 0 ? name + "_LOD0" : $"{name}_LOD{i}";
                var renderers = new List<Renderer>();
                foreach (var r in inst.GetComponentsInChildren<MeshRenderer>(true))
                {
                    if (r.gameObject.name.StartsWith("UCX_"))
                    {
                        var mc = root.AddComponent<MeshCollider>();
                        mc.sharedMesh = r.GetComponent<MeshFilter>().sharedMesh;
                        mc.convex = Str(a, "kind") == "prop";
                        Object.DestroyImmediate(r.gameObject);
                        continue;
                    }
                    r.sharedMaterials = r.sharedMaterials.Select(m =>
                        m != null && slotMats.TryGetValue(m.name, out var mm) && mm != null ? mm : m).ToArray();
                    renderers.Add(r);
                }
                float next = i + 1 < lods.Count ? Num(lods[i + 1], "screen_size", 0.1f) * 0.5f : 0.01f;
                levels.Add(new LOD(next, renderers.ToArray()));
            }
            if (levels.Count > 1) root.AddComponent<LODGroup>().SetLODs(levels.ToArray());
            foreach (var t in root.GetComponentsInChildren<Transform>(true))
                GameObjectUtility.SetStaticEditorFlags(t.gameObject, StaticEditorFlags.ContributeGI |
                    StaticEditorFlags.OccluderStatic | StaticEditorFlags.OccludeeStatic |
                    StaticEditorFlags.BatchingStatic | StaticEditorFlags.ReflectionProbeStatic);
            var path = $"{Root}/Prefabs/{name}.prefab";
            prefabs[name] = PrefabUtility.SaveAsPrefabAsset(root, path);
            Object.DestroyImmediate(root);
        }

        // ------------------------------------------------------------------ level
        static void BuildLevel(bool shots, bool bake)
        {
            var lv = (Dictionary<string, object>)Json.Parse(File.ReadAllText(Path.Combine(exportDir, "level.json")));
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var rooms = new Dictionary<string, Transform>();
            Transform Group(string room, string kind)
            {
                var key = room + "/" + kind;
                if (rooms.TryGetValue(key, out var t)) return t;
                if (!rooms.TryGetValue(room, out var r))
                {
                    r = new GameObject(room).transform;
                    rooms[room] = r;
                }
                t = new GameObject(kind).transform;
                t.SetParent(r, false);
                rooms[key] = t;
                return t;
            }

            var missing = new HashSet<string>();
            foreach (Dictionary<string, object> inst in List(lv, "instances"))
            {
                if (!prefabs.TryGetValue(Str(inst, "asset"), out var prefab)) { missing.Add(Str(inst, "asset")); continue; }
                var go = (GameObject)PrefabUtility.InstantiatePrefab(prefab, scene);
                go.name = Str(inst, "name");
                go.transform.SetParent(Group(Str(inst, "room"), Str(inst, "kind")), false);
                go.transform.SetPositionAndRotation(Pos(inst["pos"]), Yaw(Num(inst, "yaw", 0)));
                go.transform.localScale = Vector3.one * Num(inst, "scale", 1f);
                if (inst.ContainsKey("materials")) OverrideMaterials(go, Str(inst, "asset"), Dict(inst, "materials"));
            }
            if (missing.Count > 0) Warn("not imported, skipped: " + string.Join(", ", missing));

            foreach (Dictionary<string, object> l in List(lv, "lights")) Light(l, Group(Str(l, "room"), "Lights"));
            BuildEnvironment(lv);
            foreach (Dictionary<string, object> v in List(lv, "volumes")) Probes(v, Group(Str(v, "room"), "Volumes"));
            foreach (Dictionary<string, object> d in List(lv, "decals")) Decal(d, Group(Str(d, "room"), "Decals"));
            var cams = new Dictionary<string, Camera>();
            foreach (Dictionary<string, object> c in List(lv, "cameras"))
            {
                var go = new GameObject("Cam_" + Str(c, "id"));
                go.transform.SetParent(Group(Str(c, "room"), "Cameras"), false);
                go.transform.SetPositionAndRotation(Pos(c["pos"]), Look(c["dir"]));
                var cam = go.AddComponent<Camera>();
                cam.fieldOfView = Camera.HorizontalToVerticalFieldOfView(Num(c, "fov", 90), 16f / 9f);
                cam.nearClipPlane = 0.05f;
                cam.enabled = false;
                cams[Str(c, "id")] = cam;
            }
            if (lv.TryGetValue("player_start", out var psObj) && psObj is Dictionary<string, object> ps)
            {
                var go = new GameObject("Main Camera") { tag = "MainCamera" };
                var p = Pos(ps["pos"]) + Vector3.up * 1.6f;
                go.transform.SetPositionAndRotation(p, Yaw(Num(ps, "yaw", 0)));
                go.AddComponent<Camera>().nearClipPlane = 0.05f;
                go.AddComponent<AudioListener>();
            }
            Directory.CreateDirectory(Root + "/Scenes");
            var path = $"{Root}/Scenes/{Str(lv, "name")}.unity";
            EditorSceneManager.SaveScene(scene, path);
            Log($"scene {path}: {List(lv, "instances").Count} instances, {List(lv, "lights").Count} lights");
            if (bake)
            {
                Log("baking lighting (this can take a while)");
                UnityEditor.Lightmapping.Bake();
                EditorSceneManager.SaveScene(scene, path);
            }
            if (shots) Shots(cams);
        }

        static readonly Dictionary<string, string[]> slotCache = new Dictionary<string, string[]>();
        static string[] SlotNames(string asset)
        {
            if (slotCache.TryGetValue(asset, out var s)) return s;
            var a = assets[asset];
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(UnityPath(a, Str(Lods(a)[0], "file")));
            var r = model.GetComponentsInChildren<MeshRenderer>().First(x => !x.gameObject.name.StartsWith("UCX_"));
            return slotCache[asset] = r.sharedMaterials.Select(m => m != null ? m.name : "").ToArray();
        }

        static void OverrideMaterials(GameObject go, string asset, Dictionary<string, object> map)
        {
            var slots = SlotNames(asset);
            foreach (var r in go.GetComponentsInChildren<MeshRenderer>())
            {
                var mats = r.sharedMaterials;
                for (int i = 0; i < mats.Length && i < slots.Length; i++)
                    if (map.TryGetValue(slots[i], out var mid) && materials.TryGetValue((string)mid, out var m))
                        mats[i] = m;
                r.sharedMaterials = mats;
            }
        }

        static void Light(Dictionary<string, object> l, Transform parent)
        {
            var go = new GameObject(Str(l, "name"));
            go.transform.SetParent(parent, false);
            go.transform.SetPositionAndRotation(Pos(l["pos"]), Look(l["dir"]));
            var light = go.AddComponent<Light>();
            var type = Str(l, "type");
            var mobility = Str(l, "mobility");
            light.type = type == "spot" ? LightType.Spot : (type == "rect" && mobility == "static" ? LightType.Rectangle : LightType.Point);
            if (light.type == LightType.Spot) light.spotAngle = Num(l, "cone", 60);
            if (light.type == LightType.Rectangle) { var sz = Floats(l["size"]); light.areaSize = new Vector2(sz[0], sz[1]); }
            light.range = Num(l, "radius", 8);
            light.useColorTemperature = true;
            light.colorTemperature = Num(l, "kelvin", 4500);
            if (l.ContainsKey("color") && l["color"] != null) light.color = ColorOf(l, "color", Color.white);
            float lumens = Num(l, "lumens", 1500);
            // URP / Built-in have no physical units: candela (lm / 4 pi) scaled into the usual 1-10 range
            light.intensity = pipeline == Pipeline.HDRP ? lumens : lumens / (4 * Mathf.PI * 25f);
            SetLightUnit(light, "Lumen");
            light.shadows = Bool(l, "shadows") ? LightShadows.Soft : LightShadows.None;
            light.lightmapBakeType = mobility == "static" ? LightmapBakeType.Baked : mobility == "movable" ? LightmapBakeType.Realtime : LightmapBakeType.Mixed;
        }

        static void SetLightUnit(Light light, string unit)
        {
            if (pipeline != Pipeline.HDRP) return;
            var p = typeof(Light).GetProperty("lightUnit");
            if (p != null) p.SetValue(light, Enum.Parse(p.PropertyType, unit));
        }

        static void BuildEnvironment(Dictionary<string, object> lv)
        {
            var L = Dict(lv, "lighting");
            bool dynamic = Str(L, "gi") != "baked";
            if (L.ContainsKey("sun"))
            {
                var sun = Dict(L, "sun");
                var go = new GameObject("Sun");
                go.transform.rotation = Look(sun["dir"]);
                var light = go.AddComponent<Light>();
                light.type = LightType.Directional;
                float lux = Num(sun, "lux", 75000);
                light.intensity = pipeline == Pipeline.HDRP ? lux : Mathf.Clamp(lux / 50000f, 0.2f, 3f);
                SetLightUnit(light, "Lux");
                light.useColorTemperature = true;
                light.colorTemperature = Num(sun, "kelvin", 5800);
                light.shadows = LightShadows.Soft;
                light.lightmapBakeType = dynamic ? LightmapBakeType.Realtime : LightmapBakeType.Mixed;
                RenderSettings.sun = light;
            }
            if (pipeline != Pipeline.HDRP && (!L.ContainsKey("sky") || Bool(L, "sky")))
            {
                var sky = new Material(Shader.Find("Skybox/Procedural"));
                AssetDatabase.CreateAsset(sky, Root + "/Materials/Sky.mat");
                RenderSettings.skybox = sky;
                RenderSettings.ambientMode = AmbientMode.Skybox;
            }
            if (L.ContainsKey("fog"))
            {
                var fog = Dict(L, "fog");
                RenderSettings.fog = true;
                RenderSettings.fogMode = FogMode.ExponentialSquared;
                RenderSettings.fogDensity = Num(fog, "density", 0.02f);
                RenderSettings.fogColor = ColorOf(fog, "color", new Color(0.7f, 0.75f, 0.8f));
            }
            PostVolume(L.ContainsKey("post") ? Dict(L, "post") : new Dictionary<string, object>());
        }

        static void PostVolume(Dictionary<string, object> post)
        {
            var volumeType = Type.GetType("UnityEngine.Rendering.Volume, Unity.RenderPipelines.Core.Runtime");
            var profileType = Type.GetType("UnityEngine.Rendering.VolumeProfile, Unity.RenderPipelines.Core.Runtime");
            if (volumeType == null || pipeline == Pipeline.Builtin) { Warn("no SRP volume system: post-processing skipped"); return; }
            var go = new GameObject("PostProcess");
            var vol = go.AddComponent(volumeType);
            volumeType.GetProperty("isGlobal")?.SetValue(vol, true);
            var profile = ScriptableObject.CreateInstance(profileType);
            AssetDatabase.CreateAsset(profile, Root + "/Materials/PostProfile.asset");
            volumeType.GetField("sharedProfile").SetValue(vol, profile);
            string ns = pipeline == Pipeline.URP ? "UnityEngine.Rendering.Universal" : "UnityEngine.Rendering.HighDefinition";
            string asm = pipeline == Pipeline.URP ? "Unity.RenderPipelines.Universal.Runtime" : "Unity.RenderPipelines.HighDefinition.Runtime";
            object Add(string type)
            {
                var t = Type.GetType($"{ns}.{type}, {asm}");
                if (t == null) return null;
                var comp = profileType.GetMethod("Add", new[] { typeof(Type), typeof(bool) }).Invoke(profile, new object[] { t, false });
                AssetDatabase.AddObjectToAsset((Object)comp, profile);
                return comp;
            }
            void Set(object comp, string field, object value)
            {
                if (comp == null) return;
                var param = comp.GetType().GetField(field)?.GetValue(comp);
                if (param == null) return;
                var valueType = param.GetType().GetProperty("value").PropertyType;
                var v = valueType.IsEnum ? Enum.Parse(valueType, value.ToString()) : Convert.ChangeType(value, valueType);
                param.GetType().GetMethod("Override", new[] { valueType }).Invoke(param, new[] { v });
            }
            Set(Add("Tonemapping"), "mode", "ACES");
            if (post.ContainsKey("bloom")) Set(Add("Bloom"), "intensity", Num(post, "bloom", 0.3f));
            if (post.ContainsKey("vignette")) Set(Add("Vignette"), "intensity", Num(post, "vignette", 0.25f));
            if (post.ContainsKey("grain")) Set(Add("FilmGrain"), "intensity", Num(post, "grain", 0.05f));
            var ca = Add("ColorAdjustments");
            if (post.ContainsKey("saturation")) Set(ca, "saturation", (Num(post, "saturation", 1) - 1) * 100);
            if (post.ContainsKey("contrast")) Set(ca, "contrast", (Num(post, "contrast", 1) - 1) * 100);
            if (post.ContainsKey("white_balance")) Set(Add("WhiteBalance"), "temperature", (Num(post, "white_balance", 6500) - 6500) / 30f);
            if (pipeline == Pipeline.HDRP && post.ContainsKey("exposure"))
            {
                var e = Floats(post["exposure"]);
                var ex = Add("Exposure");
                Set(ex, "mode", "Automatic");
                Set(ex, "limitMin", e[0]);
                Set(ex, "limitMax", e[1]);
            }
            EditorUtility.SetDirty(profile);
        }

        static void Probes(Dictionary<string, object> v, Transform parent)
        {
            if (Bool(v, "outdoor")) return;
            var a = Pos(v["min"]); var b = Pos(v["max"]);
            var bounds = new Bounds((a + b) / 2, Vector3.zero);
            bounds.Encapsulate(a); bounds.Encapsulate(b);
            var go = new GameObject(Str(v, "name") + "_Probes");
            go.transform.SetParent(parent, false);
            go.transform.position = bounds.center;
            var rp = go.AddComponent<ReflectionProbe>();
            rp.size = bounds.size;
            rp.boxProjection = true;
            rp.mode = ReflectionProbeMode.Baked;
            var lp = go.AddComponent<LightProbeGroup>();
            var pts = new List<Vector3>();
            for (float x = bounds.min.x + 1; x <= bounds.max.x - 0.9f; x += 2)
                for (float z = bounds.min.z + 1; z <= bounds.max.z - 0.9f; z += 2)
                    foreach (var y in new[] { 0.5f, 2.0f })
                        pts.Add(new Vector3(x, bounds.min.y + y, z) - bounds.center);
            lp.probePositions = pts.ToArray();
        }

        static void Decal(Dictionary<string, object> d, Transform parent)
        {
            if (!materials.TryGetValue(Str(d, "asset"), out var mat)) return;
            string typeName = pipeline == Pipeline.URP ? "UnityEngine.Rendering.Universal.DecalProjector, Unity.RenderPipelines.Universal.Runtime"
                            : "UnityEngine.Rendering.HighDefinition.DecalProjector, Unity.RenderPipelines.HighDefinition.Runtime";
            var t = Type.GetType(typeName);
            if (t == null) return;
            var go = new GameObject(Str(d, "name"));
            go.transform.SetParent(parent, false);
            var n = Floats(d["normal"]);
            go.transform.SetPositionAndRotation(Pos(d["pos"]), Look(new List<object> { -n[0], -n[1], -n[2] }, d["up"]));
            var proj = go.AddComponent(t);
            var size = Floats(d["size"]);
            float depth = Num(d, "depth", 0.3f);
            t.GetProperty("material").SetValue(proj, mat);
            t.GetProperty("size").SetValue(proj, new Vector3(size[0], size[1], depth));
            t.GetProperty("pivot")?.SetValue(proj, Vector3.zero);
        }

        static void Shots(Dictionary<string, Camera> cams)
        {
            var dir = Path.Combine(exportDir, "shots", "unity");
            Directory.CreateDirectory(dir);
            var rt = new RenderTexture(1600, 900, 24, RenderTextureFormat.ARGB32);
            var tex = new Texture2D(1600, 900, TextureFormat.RGB24, false);
            foreach (var kv in cams)
            {
                kv.Value.targetTexture = rt;
                kv.Value.Render();
                RenderTexture.active = rt;
                tex.ReadPixels(new Rect(0, 0, 1600, 900), 0, 0);
                tex.Apply();
                File.WriteAllBytes(Path.Combine(dir, kv.Key + ".png"), tex.EncodeToPNG());
                kv.Value.targetTexture = null;
            }
            RenderTexture.active = null;
            Object.DestroyImmediate(rt);
            Log($"shots -> {dir}");
        }

        // ------------------------------------------------------------------ JSON helpers
        static string Str(Dictionary<string, object> d, string k) => d.TryGetValue(k, out var v) && v != null ? v.ToString() : null;
        static float Num(Dictionary<string, object> d, string k, float def) => d.TryGetValue(k, out var v) && v != null ? Convert.ToSingle(v) : def;
        static double Num(Dictionary<string, object> d, string k, double def) => d.TryGetValue(k, out var v) && v != null ? Convert.ToDouble(v) : def;
        static bool Bool(Dictionary<string, object> d, string k) => d.TryGetValue(k, out var v) && v is bool b && b;
        static Dictionary<string, object> Dict(Dictionary<string, object> d, string k) =>
            d.TryGetValue(k, out var v) && v is Dictionary<string, object> r ? r : new Dictionary<string, object>();
        static List<object> List(Dictionary<string, object> d, string k) => d.TryGetValue(k, out var v) && v is List<object> l ? l : new List<object>();
        static List<Dictionary<string, object>> Lods(Dictionary<string, object> a) => List(a, "lods").Cast<Dictionary<string, object>>().ToList();
        static float[] Floats(object o) => ((List<object>)o).Select(Convert.ToSingle).ToArray();
        static Color ColorOf(Dictionary<string, object> d, string k, Color def)
        {
            if (!d.TryGetValue(k, out var v) || !(v is List<object>)) return def;
            var f = Floats(v);
            return new Color(f[0], f[1], f[2], f.Length > 3 ? f[3] : 1f);
        }
    }

    /// <summary>Minimal JSON reader: objects -> Dictionary, arrays -> List, numbers -> double.</summary>
    static class Json
    {
        public static object Parse(string s) { int i = 0; return Value(s, ref i); }

        static void Ws(string s, ref int i) { while (i < s.Length && char.IsWhiteSpace(s[i])) i++; }

        static object Value(string s, ref int i)
        {
            Ws(s, ref i);
            char c = s[i];
            if (c == '{')
            {
                var d = new Dictionary<string, object>();
                i++; Ws(s, ref i);
                if (s[i] == '}') { i++; return d; }
                while (true)
                {
                    Ws(s, ref i);
                    var k = (string)Value(s, ref i);
                    Ws(s, ref i); i++;                       // ':'
                    d[k] = Value(s, ref i);
                    Ws(s, ref i);
                    if (s[i++] == '}') return d;            // else ','
                }
            }
            if (c == '[')
            {
                var l = new List<object>();
                i++; Ws(s, ref i);
                if (s[i] == ']') { i++; return l; }
                while (true)
                {
                    l.Add(Value(s, ref i));
                    Ws(s, ref i);
                    if (s[i++] == ']') return l;
                }
            }
            if (c == '"')
            {
                var sb = new StringBuilder();
                i++;
                while (s[i] != '"')
                {
                    if (s[i] == '\\')
                    {
                        i++;
                        char e = s[i];
                        if (e == 'u') { sb.Append((char)Convert.ToInt32(s.Substring(i + 1, 4), 16)); i += 4; }
                        else sb.Append(e == 'n' ? '\n' : e == 't' ? '\t' : e == 'r' ? '\r' : e == 'b' ? '\b' : e == 'f' ? '\f' : e);
                    }
                    else sb.Append(s[i]);
                    i++;
                }
                i++;
                return sb.ToString();
            }
            if (s.Substring(i).StartsWith("true")) { i += 4; return true; }
            if (s.Substring(i).StartsWith("false")) { i += 5; return false; }
            if (s.Substring(i).StartsWith("null")) { i += 4; return null; }
            int start = i;
            while (i < s.Length && "+-0123456789.eE".IndexOf(s[i]) >= 0) i++;
            return double.Parse(s.Substring(start, i - start), System.Globalization.CultureInfo.InvariantCulture);
        }
    }

    /// <summary>Import settings for everything under Assets/T2S/Imported (runs before Unity imports the file).</summary>
    class T2SPostprocessor : AssetPostprocessor
    {
        void OnPreprocessTexture()
        {
            if (!assetPath.StartsWith("Assets/T2S/Imported")) return;
            var ti = (TextureImporter)assetImporter;
            var file = Path.GetFileNameWithoutExtension(assetPath);
            ti.mipmapEnabled = true;
            ti.streamingMipmaps = true;
            ti.anisoLevel = 4;
            if (file.EndsWith("_N")) ti.textureType = TextureImporterType.NormalMap;
            else if (file.EndsWith("_MASK") || file.EndsWith("_ORM") || file == "T_Macro") ti.sRGBTexture = false;
            if (file.EndsWith("_BC")) ti.alphaIsTransparency = assetPath.Contains("/decals/");
        }

        void OnPreprocessModel()
        {
            if (!assetPath.StartsWith("Assets/T2S/Imported")) return;
            var mi = (ModelImporter)assetImporter;
            mi.globalScale = 1f;
            mi.useFileScale = true;
            mi.bakeAxisConversion = true;
            mi.importCameras = false;
            mi.importLights = false;
            mi.importAnimation = false;
            mi.animationType = ModelImporterAnimationType.None;
            mi.materialImportMode = ModelImporterMaterialImportMode.ImportViaMaterialDescription;
            mi.importNormals = ModelImporterNormals.Import;
            mi.importTangents = ModelImporterTangents.CalculateMikk;
            mi.generateSecondaryUV = true;
            mi.isReadable = false;
            mi.addCollider = false;
        }
    }
}
