"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { LDrawLoader } from "three/examples/jsm/loaders/LDrawLoader.js";

// CDN-hosted complete LDraw parts library (gkjohnson mirror — the same one
// Three.js's official examples use). Lets the viewer render any LDraw model
// with zero local install. We'll move to a self-hosted mirror (Supabase Storage
// or Vercel static assets) before public launch.
const LDRAW_CDN =
  "https://raw.githubusercontent.com/gkjohnson/ldraw-parts-library/master/complete/ldraw/";

/**
 * LdrawViewer — Three.js viewer for LDraw (.ldr / .mpd) files.
 *
 * Renders each brick as its own addressable object (NO merging). This is
 * required for explode-view, step isolation, click-to-inspect, and the
 * generated step-by-step instructions in later phases.
 *
 * Features:
 *   - OrbitControls (orbit / pan / zoom, touch friendly)
 *   - Preset camera angles (front / side / top / iso / back)
 *   - Background toggle (dark / studio / light)
 *   - Explode-view slider — pushes EACH BRICK outward from model center
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
    bricks?: THREE.Object3D[];      // individual brick nodes for per-brick ops
    steps?: THREE.Group[];
    bbox?: THREE.Box3;
    raf?: number;
  }>({});

  const [bg, setBg] = useState<"dark" | "studio" | "light">("dark");
  const [explode, setExplode] = useState(0); // 0..1
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

    // Soft 3-light setup so studs read well from any angle.
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
    // smoothNormals = false preserves the visible plate/stud edges that make
    // LEGO actually look like LEGO.
    (loader as any).smoothNormals = false;

    // Default sample: the car.mpd file Three.js uses in its own LDrawLoader
    // example. Verified to be 200 OK and to render correctly. Small enough to
    // load fast (~30 bricks) but real LEGO. The parts library itself is served
    // from the gkjohnson CDN (verified 200 on parts/3001.dat etc).
    const url =
      modelUrl ??
      "https://raw.githubusercontent.com/mrdoob/three.js/master/examples/models/ldraw/officialLibrary/models/car.ldr_Packed.mpd";

    loader.load(
      url,
      (group) => {
        // Remove any prior model.
        if (stateRef.current.model) {
          scene.remove(stateRef.current.model);
        }

        // CRITICAL: do NOT merge meshes. Each brick must remain its own
        // addressable object so we can drive explode, step isolation, hover,
        // and click. (Earlier version called LDrawUtils.mergeObject which
        // collapsed all bricks of the same color into a single mesh — that's
        // why the model moved as a single rigid blob.)

        // LDraw uses Y-down; flip to Y-up so our camera/controls make sense.
        group.rotation.x = Math.PI;

        scene.add(group);
        stateRef.current.model = group;

        // Identify per-brick nodes. LDrawLoader emits a Group per .dat reference
        // in the source file; bricks are the leaf groups whose children are
        // meshes (no further nested groups).
        const bricks: THREE.Object3D[] = [];
        group.traverse((node) => {
          const isLeafGroup =
            (node as any).isGroup &&
            node.children.length > 0 &&
            node.children.every((c) => (c as any).isMesh);
          if (isLeafGroup) bricks.push(node);
        });

        // Fallback for pre-merged MPDs with no brick-level groups: treat each
        // mesh as its own brick.
        if (bricks.length === 0) {
          group.traverse((node) => {
            if ((node as any).isMesh) bricks.push(node);
          });
        }

        stateRef.current.bricks = bricks;
        setBrickCount(bricks.length);

        // Compute bbox + cache per-brick reference data for explode.
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

        // Build step list (LDraw files may group bricks into authored steps).
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

        // Fit camera to model bounds.
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
          "Couldn't load this model. Check the browser console for the underlying error — most often a network issue fetching part .dat files from the LDraw mirror."
        );
        setLoading(false);
      }
    );
  }, [modelUrl, partsLibraryPath]);

  // -------- Explode view (per brick) --------
  useEffect(() => {
    const bricks = stateRef.current.bricks;
    const bbox = stateRef.current.bbox;
    if (!bricks || bricks.length === 0 || !bbox) return;

    const size = new THREE.Vector3();
    bbox.getSize(size);
    const maxDim = Math.max(size.x, size.y, size.z);
    const strength = explode * maxDim * 0.6;

    bricks.forEach((b) => {
      const orig: THREE.Vector3 | undefined = b.userData.originalPosition;
      const offsetDir: THREE.Vector3 | undefined = b.userData.worldCenterOffset;
      if (!orig || !offsetDir) return;
      const dir = offsetDir.clone();
      if (dir.lengthSq() < 1e-6) {
        // Brick is exactly at center — pick a stable arbitrary direction so
        // it still separates from neighbors as you slide.
        dir.set(0, 1, 0);
      } else {
        dir.normalize();
      }
      b.position.copy(orig).addScaledVector(dir, strength);
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

      {/* Top-left: status */}
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

      {/* Top-right: viewing controls */}
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

      {/* Bottom: explode + step */}
      <div className="absolute bottom-3 left-3 right-3 flex flex-col gap-2">
        <div className="flex items-center gap-3 bg-neutral-900/80 rounded-md px-3 py-2 text-xs">
          <span className="w-16">Explode</span>
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
            <span className="w-16">Step</span>
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
