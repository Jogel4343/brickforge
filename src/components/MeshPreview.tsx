"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

/**
 * MeshPreview — minimal viewer for the .glb that Meshy returns.
 *
 * Used in the /design flow so the user can see the raw 3D mesh before we
 * convert it to LEGO bricks. Once LegoGPT integration lands (Week 4), we'll
 * keep this as an optional "see the source mesh" toggle but the primary
 * output will be the LDraw model in LdrawViewer.
 */
export default function MeshPreview({
  glbUrl,
  className = "",
}: {
  glbUrl: string;
  className?: string;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0b0b0f);

    const camera = new THREE.PerspectiveCamera(
      45,
      container.clientWidth / container.clientHeight,
      0.01,
      1000
    );
    camera.position.set(2, 2, 3);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setSize(container.clientWidth, container.clientHeight);
    container.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;

    scene.add(new THREE.AmbientLight(0xffffff, 0.7));
    const dir = new THREE.DirectionalLight(0xffffff, 0.9);
    dir.position.set(5, 8, 5);
    scene.add(dir);

    const loader = new GLTFLoader();
    let raf = 0;
    loader.load(
      glbUrl,
      (gltf) => {
        scene.add(gltf.scene);
        const bbox = new THREE.Box3().setFromObject(gltf.scene);
        const size = new THREE.Vector3();
        const center = new THREE.Vector3();
        bbox.getSize(size);
        bbox.getCenter(center);
        const maxDim = Math.max(size.x, size.y, size.z);
        camera.position.set(
          center.x + maxDim * 1.2,
          center.y + maxDim * 0.8,
          center.z + maxDim * 1.5
        );
        controls.target.copy(center);
        controls.update();
      },
      undefined,
      (err) => console.error("GLTF load failed", err)
    );

    const onResize = () => {
      camera.aspect = container.clientWidth / container.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(container.clientWidth, container.clientHeight);
    };
    window.addEventListener("resize", onResize);

    const animate = () => {
      controls.update();
      renderer.render(scene, camera);
      raf = requestAnimationFrame(animate);
    };
    animate();

    return () => {
      window.removeEventListener("resize", onResize);
      cancelAnimationFrame(raf);
      controls.dispose();
      renderer.dispose();
      if (renderer.domElement.parentElement === container) {
        container.removeChild(renderer.domElement);
      }
    };
  }, [glbUrl]);

  return <div ref={containerRef} className={`relative w-full h-full ${className}`} />;
}
