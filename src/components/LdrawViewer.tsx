"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { LDrawLoader } from "three/examples/jsm/loaders/LDrawLoader.js";

// CDN-hosted complete LDraw parts library (gkjohnson mirror — same one used by
// Three.js's official examples). Lets the viewer render any LDraw model with
// zero local install. We'll move to a self-hosted mirror (Supabase Storage or
// Vercel static assets) before public launch.
const LDRAW_CDN =
  "https://raw.githubusercontent.com/gkjohnson/ldraw-parts-library/master/complete/ldraw/";

/**
 * LdrawViewer — Three.js viewer for LDraw (.ldr / .mpd) files.
 *
 * Renders each brick as its own addressable object (NO mesh merging) so we
 * can drive per-brick UX: explode, step isolation, click-to-inspect, and the
 * generated step-by-step instructions in later phases.
 *
 * Features:
 *   - OrbitControls (orbit / pan / zoom, touch-friendly)
 *   - Preset camera angles (front / side / top / iso / back)
 *   - Background toggle (dark / studio / light)
 *   - Explode-view slider (beta — radial distance-scaled separation)
 *   - Step-through slider (when the LDraw file has authored construction steps)
 *   - Screenshot capture
 *   - Brick count display
 */
export default function LdrawViewer({
  modelUrl,
  partsLibraryPath = LDRAW_CDN,
  className = "",
}: {
  modelUrl?: string;
  partsLibraryPath?: string;
  className?: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const stateRef = useRef<{
    renderer?: THREE.WebGLRenderer;
    scene?: THREE.Scene;
    camera?: THREE.PerspectiveCamera;
    controls?: OrbitControls;
    model?: THREE.Group;
    bricks?: THREE.Object3D[];
    steps?: THREE.Group[];
    bbox?: THREE.Box3;
    raf?: number;
  }>({});

  const [bg, setBg] = useState<"dark" | "studio" | "light">("dark");
  const [explode, setExplode] = useState(0);
  const [stepIndex, setStepIndex] = useState<number | null>(null);
  const [totalSteps, setTotalSteps] = useState(0);
  const [brickCount, setBrickCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // -------- Init scene --------
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0b0b0f);

    const camera = new THREE.PerspectiveCamera(
      45,
      container.clientWidth / container.clientHeight,
      1,
      10000
    );
    camera.position.set(150, 200, 250);

    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setSize(container.clientWidth, container.clientHeight);
    container.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    const ambient = new THREE.AmbientLight(0xffffff, 0.6);
    const key = new THREE.DirectionalLight(0xffffff, 0.9);
    key.position.set(200, 400, 300);
    const fill = new THREE.DirectionalLight(0xffffff, 0.4);
    fill.position.set(-300, 200, -200);
    scene.add(ambient, key, fill);

    stateRef.current = { renderer, scene, camera, controls };

    const onResize = () => {
      if (!container) return;
      camera.aspect = container.clientWidth / container.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(container.clientWidth, container.clientHeight);
    };
    window.addEventListener("resize", onResize);

    const animate = () => {
      controls.update();
      renderer.render(scene, camera);
      stateRef.current.raf = requestAnimationFrame(animate);
    };
    animate();

    return () => {
      window.removeEventListener("resize", onResize);
      if (stateRef.current.raf) cancelAnimationFrame(stateRef.current.raf);
      controls.dispose();
      renderer.dispose();
      if (renderer.domElement.parentElement === container) {
        container.removeChild(renderer.domElement);
      }
    };
  }, []);

  // -------- Background --------
  useEffect(() => {
    const s = stateRef.current.scene;
    if (!s) return;
    const colors = { dark: 0x0b0b0f, studio: 0x222228, light: 0xf5f5f5 };
    s.background = new THREE.Color(colors[bg]);
  }, [bg]);

  // -------- Load model --------
  useEffect(() => {
    const { scene } = stateRef.current;
    if (!scene) return;

    setLoading(true);
    setLoadError(null);
    setBrickCount(0);
    setTotalSteps(0);
    setStepIndex(null);

    const loader = new LDrawLoader();
    loader.setPartsLibraryPath(partsLibraryPath);
    (loader as any).smoothNormals = false;

    const url =
      modelUrl ??
      "https://raw.githubusercontent.com/mrdoob/three.js/master/examples/models/ldraw/officialLibrary/models/car.ldr_Packed.mpd";

    // CRITICAL FIX: preload the LDraw color palette (LDConfig.ldr) before
    // loading the model. Without this, every colour code that isn't already
    // embedded in the file falls back to LDrawLoader.missingColorMaterial
    // (magenta 0xFF00FF). Packed .mpd files embed materials so this step
    // was silently unnecessary for the default demo car; plain .ldr files
    // need it.
    const doLoad = () => loader.load(
      url,
      (group) => {
        if (stateRef.current.model) {
          scene.remove(stateRef.current.model);
        }

        // CRITICAL FIX 1: strip wireframe edges. LDrawLoader emits LineSegments
        // for the iconic LEGO black outlines on every part. These were causing
        // the "ghost wireframe" bricks the user saw — line geometry stays put
        // when we reposition brick meshes (lines are siblings of mesh groups,
        // not part of the bricks themselves). Removing them cleans the visual
        // and also speeds up rendering.
        const toRemove: THREE.Object3D[] = [];
        group.traverse((node) => {
          if ((node as any).isLineSegments || (node as any).isLine) {
            toRemove.push(node);
          }
        });
        toRemove.forEach((n) => n.parent?.remove(n));

        // LDraw uses Y-down; flip to Y-up so camera/controls make sense.
        group.rotation.x = Math.PI;

        scene.add(group);
        stateRef.current.model = group;

        // CRITICAL FIX 2: smarter brick detection. Previous heuristic was
        // "leaf group whose children are all meshes" — but with edge geometry
        // (now stripped) some bricks didn't match. Three layers of robustness:
        //   (a) Group has LDraw brick metadata (partType / colorCode)
        //   (b) Group whose direct children are all meshes
        //   (c) Each mesh as its own brick (last-resort fallback)
        const bricks: THREE.Object3D[] = [];

        // Layer (a)
        group.traverse((node) => {
          if (!(node as any).isGroup) return;
          const ud = (node as any).userData || {};
          const hasMetadata =
            ud.partType !== undefined ||
            ud.colorCode !== undefined ||
            ud.constructionStep !== undefined;
          if (hasMetadata && node.children.length > 0) {
            bricks.push(node);
          }
        });

        // Layer (b)
        if (bricks.length === 0) {
          group.traverse((node) => {
            if (!(node as any).isGroup) return;
            const leaf =
              node.children.length > 0 &&
              node.children.every((c) => (c as any).isMesh);
            if (leaf) bricks.push(node);
          });
        }

        // Layer (c)
        if (bricks.length === 0) {
          group.traverse((node) => {
            if ((node as any).isMesh) bricks.push(node);
          });
        }

        stateRef.current.bricks = bricks;
        setBrickCount(bricks.length);

        // Cache per-brick origin + offset-from-center for the explode pass.
        const bbox = new THREE.Box3().setFromObject(group);
        stateRef.current.bbox = bbox;
        const center = new THREE.Vector3();
        bbox.getCenter(center);
        bricks.forEach((b) => {
          const worldPos = new THREE.Vector3();
          b.getWorldPosition(worldPos);
          b.userData.originalPosition = b.position.clone();
          b.userData.worldCenterOffset = worldPos.clone().sub(center);
        });

        // Build authored-step list (LDraw files can group bricks into steps).
        const steps: THREE.Group[] = [];
        group.traverse((child) => {
          if (
            (child as any).isGroup &&
            (child as any).userData?.constructionStep !== undefined
          ) {
            steps.push(child as THREE.Group);
          }
        });
        stateRef.current.steps = steps;
        setTotalSteps(steps.length);

        // Fit camera to model.
        const size = new THREE.Vector3();
        bbox.getSize(size);
        const maxDim = Math.max(size.x, size.y, size.z);
        const cam = stateRef.current.camera!;
        const ctrl = stateRef.current.controls!;
        cam.position.set(
          center.x + maxDim * 1.2,
          center.y + maxDim * 1.0,
          center.z + maxDim * 1.5
        );
        ctrl.target.copy(center);
        ctrl.update();

        setLoading(false);
      },
      undefined,
      (err) => {
        console.error("LDraw load failed", err);
        setLoadError(
          "Couldn't load this model. Check the browser console — most likely a network issue fetching part .dat files from the LDraw mirror."
        );
        setLoading(false);
      }
    );
    loader
      .preloadMaterials(`${partsLibraryPath}LDConfig.ldr`)
      .then(doLoad)
      .catch((err: unknown) => {
        // Non-fatal — attempt the load anyway; colours will just fall back.
        // eslint-disable-next-line no-console
        console.warn("LDrawLoader: preloadMaterials failed", err);
        doLoad();
      });
  }, [modelUrl, partsLibraryPath]);

  // -------- Explode view (radial, distance-scaled) --------
  // Standard Three.js community pattern (DevDojo / r3f tutorials use the same
  // approach). Each brick moves along its center→position vector, scaled by
  // distance from center. Closer bricks displace less; farther bricks fly free.
  //
  // Note: this is intentionally a "beta" / demo feature. Real LEGO products
  // use step-by-step build progression (which Week 6 wires up) rather than
  // radial explode. We keep the slider for the demo factor and stop polishing
  // it here — there's a clear ceiling for radial explode quality on dense
  // brick clouds, and chasing it costs project momentum.
  useEffect(() => {
    const bricks = stateRef.current.bricks;
    const bbox = stateRef.current.bbox;
    if (!bricks || bricks.length === 0 || !bbox) return;

    const factor = explode * 1.5;

    bricks.forEach((b) => {
      const orig: THREE.Vector3 | undefined = b.userData.originalPosition;
      const offsetFromCenter: THREE.Vector3 | undefined = b.userData.worldCenterOffset;
      if (!orig || !offsetFromCenter) return;

      const dist = offsetFromCenter.length();
      if (dist < 1e-6) {
        b.position.copy(orig);
        return;
      }
      const dir = offsetFromCenter.clone().normalize().multiplyScalar(dist * factor);
      // Account for the model's 180°-X rotation when applying world delta.
      b.position.copy(orig).add(new THREE.Vector3(dir.x, -dir.y, dir.z));
    });
  }, [explode]);

  // -------- Step visibility --------
  useEffect(() => {
    const steps = stateRef.current.steps;
    if (!steps || steps.length === 0) return;
    steps.forEach((g, i) => {
      g.visible = stepIndex === null ? true : i <= stepIndex;
    });
  }, [stepIndex]);

  // -------- Camera presets --------
  const setView = (view: "front" | "side" | "top" | "iso" | "back") => {
    const cam = stateRef.current.camera;
    const ctrl = stateRef.current.controls;
    const bbox = stateRef.current.bbox;
    if (!cam || !ctrl || !bbox) return;
    const size = new THREE.Vector3();
    const center = new THREE.Vector3();
    bbox.getSize(size);
    bbox.getCenter(center);
    const d = Math.max(size.x, size.y, size.z) * 1.6;
    const positions: Record<typeof view, THREE.Vector3> = {
      front: new THREE.Vector3(center.x, center.y, center.z + d),
      back: new THREE.Vector3(center.x, center.y, center.z - d),
      side: new THREE.Vector3(center.x + d, center.y, center.z),
      top: new THREE.Vector3(center.x, center.y + d, center.z),
      iso: new THREE.Vector3(center.x + d * 0.8, center.y + d * 0.7, center.z + d * 0.8),
    };
    cam.position.copy(positions[view]);
    ctrl.target.copy(center);
    ctrl.update();
  };

  // -------- Screenshot --------
  const screenshot = () => {
    const r = stateRef.current.renderer;
    if (!r) return;
    const dataUrl = r.domElement.toDataURL("image/png");
    const a = document.createElement("a");
    a.href = dataUrl;
    a.download = `brickforge-${Date.now()}.png`;
    a.click();
  };

  return (
    <div className={`relative w-full h-full ${className}`}>
      <div ref={containerRef} className="absolute inset-0" />

      {loading && (
        <div className="absolute top-3 left-3 rounded-md bg-neutral-900/80 px-3 py-1.5 text-sm">
          Loading model… (first load fetches parts from CDN — give it a few seconds)
        </div>
      )}
      {!loading && brickCount > 0 && (
        <div className="absolute top-3 left-3 rounded-md bg-neutral-900/80 px-3 py-1.5 text-xs">
          {brickCount} bricks
        </div>
      )}
      {loadError && (
        <div className="absolute top-3 left-3 max-w-md rounded-md bg-red-900/80 px-3 py-2 text-xs">
          {loadError}
        </div>
      )}

      <div className="absolute top-3 right-3 flex flex-col gap-2 items-end">
        <div className="flex gap-1 bg-neutral-900/80 rounded-md p-1">
          {(["front", "side", "top", "iso", "back"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className="px-2 py-1 text-xs rounded hover:bg-neutral-700"
            >
              {v}
            </button>
          ))}
        </div>
        <div className="flex gap-1 bg-neutral-900/80 rounded-md p-1">
          {(["dark", "studio", "light"] as const).map((b) => (
            <button
              key={b}
              onClick={() => setBg(b)}
              className={`px-2 py-1 text-xs rounded ${
                bg === b ? "bg-brand-600" : "hover:bg-neutral-700"
              }`}
            >
              {b}
            </button>
          ))}
        </div>
        <button
          onClick={screenshot}
          className="rounded-md bg-neutral-900/80 px-3 py-1.5 text-xs hover:bg-neutral-700"
        >
          📸 Screenshot
        </button>
      </div>

      <div className="absolute bottom-3 left-3 right-3 flex flex-col gap-2">
        <div className="flex items-center gap-3 bg-neutral-900/80 rounded-md px-3 py-2 text-xs">
          <span className="w-24">
            Explode <span className="text-neutral-500">(beta)</span>
          </span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.01}
            value={explode}
            onChange={(e) => setExplode(parseFloat(e.target.value))}
            className="flex-1"
          />
          <span className="w-8 text-right">{Math.round(explode * 100)}%</span>
        </div>
        {totalSteps > 0 && (
          <div className="flex items-center gap-3 bg-neutral-900/80 rounded-md px-3 py-2 text-xs">
            <span className="w-24">Step</span>
            <input
              type="range"
              min={0}
              max={totalSteps - 1}
              step={1}
              value={stepIndex ?? totalSteps - 1}
              onChange={(e) => setStepIndex(parseInt(e.target.value))}
              className="flex-1"
            />
            <span className="w-16 text-right">
              {(stepIndex ?? totalSteps - 1) + 1} / {totalSteps}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
