"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { LDrawLoader } from "three/examples/jsm/loaders/LDrawLoader.js";
import { LDrawUtils } from "three/examples/jsm/utils/LDrawUtils.js";

/**
 * LdrawViewer — drop-in Three.js viewer for LDraw (.ldr / .mpd) files.
 *
 * Built on Three.js's native LDrawLoader so we get the full LDraw parts library
 * support for free (no custom parser). Includes OrbitControls (orbit/pan/zoom),
 * preset camera angles, an explode-view slider, screenshot capture, background
 * toggle, and step-through controls.
 *
 * Props:
 *  - modelUrl: a publicly accessible URL to a .ldr/.mpd file. Defaults to the
 *    built-in Three.js LDraw sample so the viewer is demoable with zero setup.
 *  - partsLibraryPath: where the LDraw `parts/` and `p/` directories live.
 *    Must be relative to /public. Defaults to `/ldraw/` — drop the LDraw
 *    library there to enable rendering of arbitrary models.
 */
export default function LdrawViewer({
  modelUrl,
  partsLibraryPath = "/ldraw/",
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
    steps?: THREE.Group[];
    bbox?: THREE.Box3;
    raf?: number;
  }>({});

  const [bg, setBg] = useState<"dark" | "studio" | "light">("dark");
  const [explode, setExplode] = useState(0); // 0..1
  const [stepIndex, setStepIndex] = useState<number | null>(null);
  const [totalSteps, setTotalSteps] = useState(0);
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

    const loader = new LDrawLoader();
    // partsLibraryPath should point at the directory containing the LDraw `parts/` and `p/` folders.
    // Three.js looks them up relative to this path.
    loader.setPartsLibraryPath(partsLibraryPath);

    // Built-in sample if no model URL provided — uses Three.js's hosted example.
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

        // Merge subassemblies for performance.
        const merged = LDrawUtils.mergeObject(group);

        // LDraw uses Y-down; flip to Y-up.
        merged.rotation.x = Math.PI;

        scene.add(merged);
        stateRef.current.model = merged;

        // Build step list (LDraw groups bricks into steps when authored that way).
        const steps: THREE.Group[] = [];
        merged.traverse((child) => {
          // The LDrawLoader assigns userData.constructionStep to indicate step membership.
          // We collect step group nodes for the step-through UI.
          if ((child as any).isGroup && (child as any).userData?.constructionStep !== undefined) {
            steps.push(child as THREE.Group);
          }
        });
        stateRef.current.steps = steps;
        setTotalSteps(steps.length);

        // Fit camera to model bounds.
        const bbox = new THREE.Box3().setFromObject(merged);
        stateRef.current.bbox = bbox;
        const size = new THREE.Vector3();
        const center = new THREE.Vector3();
        bbox.getSize(size);
        bbox.getCenter(center);
        const maxDim = Math.max(size.x, size.y, size.z);
        const cam = stateRef.current.camera!;
        const ctrl = stateRef.current.controls!;
        cam.position.set(center.x + maxDim * 1.2, center.y + maxDim * 1.0, center.z + maxDim * 1.5);
        ctrl.target.copy(center);
        ctrl.update();

        setLoading(false);
      },
      undefined,
      (err) => {
        console.error("LDraw load failed", err);
        setLoadError(
          "Couldn't load this model. If you're seeing this in dev, the LDraw parts library may not be installed in /public/ldraw. Run the ingest script in worker/ingest_ldraw.py."
        );
        setLoading(false);
      }
    );
  }, [modelUrl, partsLibraryPath]);

  // -------- Explode view --------
  useEffect(() => {
    const model = stateRef.current.model;
    const bbox = stateRef.current.bbox;
    if (!model || !bbox) return;
    const center = new THREE.Vector3();
    bbox.getCenter(center);
    model.traverse((child) => {
      if ((child as any).isMesh) {
        const mesh = child as THREE.Mesh;
        if (!mesh.userData.originalPosition) {
          mesh.userData.originalPosition = mesh.position.clone();
        }
        const orig: THREE.Vector3 = mesh.userData.originalPosition;
        const dir = orig.clone().sub(center).normalize();
        const offset = dir.multiplyScalar(explode * 60);
        mesh.position.copy(orig).add(offset);
      }
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
          Loading model…
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
