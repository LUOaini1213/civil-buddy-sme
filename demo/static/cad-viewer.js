import * as THREE from './vendor/three/three.module.js';
import { OrbitControls } from './vendor/three/OrbitControls.js';
const translatedUI = new Map();
const trEngineering = (source, values) => {
  const result = (globalThis.CBI18n || globalThis.window?.CBI18n)?.t(source, values) ?? source.replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g, (match, key) => values && Object.hasOwn(values, key) ? String(values[key]) : match);
  translatedUI.set(result, { source, values });
  if (translatedUI.size > 2500) translatedUI.delete(translatedUI.keys().next().value);
  return result;
};
// Only explicitly named app-owned status nodes are refreshed here. Source text and inputs are excluded.
const relocalizeOwned = (doc, ids) => { for (const id of ids) { const node = doc.getElementById(id); if (!node) continue; const nodes = node.tagName === 'SELECT' ? [...node.options] : [node]; for (const target of nodes) { const item = translatedUI.get(target.textContent); if (item) target.textContent = trEngineering(item.source, item.values); } } };
const uiLocale = () => (globalThis.CBI18n || globalThis.window?.CBI18n)?.locale || 'zh-CN';

const COLORS = { wall: 0x85c9d6, column: 0x59d7ba, slab: 0x7a94b1, section: 0x66cdb7 };

/** Draw server-computed vertices verbatim. DXF models use Z as the vertical axis. */
export class CadViewer {
  constructor(host, onSelect, onError) {
    this.host = host;
    this.onSelect = onSelect;
    this.onError = onError;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(42, 1, .001, 100000);
    this.camera.up.set(0, 0, 1);
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.domElement.setAttribute('aria-label', trEngineering("可旋转的三维几何模型；构件详情也可从二维轮廓选择"));
    this.renderer.domElement.setAttribute('role', 'img');
    this.renderer.domElement.addEventListener('webglcontextlost', (event) => {
      event.preventDefault();
      this.onError(trEngineering("三维显示上下文已丢失。模型数据仍保留，可导出；刷新页面可重新初始化查看器。"));
    });
    host.prepend(this.renderer.domElement);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = false;
    this.controls.addEventListener('change', () => this.render());
    this.scene.add(new THREE.HemisphereLight(0xe3f6ff, 0x33465a, 2.3));
    const key = new THREE.DirectionalLight(0xffffff, 2.8);
    key.position.set(4, -6, 10);
    this.scene.add(key);
    const fill = new THREE.DirectionalLight(0x79b8de, 1.2);
    fill.position.set(-6, 4, 5);
    this.scene.add(fill);
    this.group = new THREE.Group();
    this.scene.add(this.group);
    this.meshes = [];
    this.edgesVisible = true;
    this.selected = null;
    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this.startPoint = null;
    this.renderer.domElement.addEventListener('pointerdown', (event) => {
      this.startPoint = { x: event.clientX, y: event.clientY, button: event.button };
    });
    this.renderer.domElement.addEventListener('pointerup', (event) => {
      const start = this.startPoint;
      this.startPoint = null;
      if (!start || start.button !== 0 || Math.hypot(start.x - event.clientX, start.y - event.clientY) > 5) return;
      const rect = this.renderer.domElement.getBoundingClientRect();
      this.pointer.set((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1);
      this.raycaster.setFromCamera(this.pointer, this.camera);
      const hit = this.raycaster.intersectObjects(this.meshes, false)[0];
      this.select(hit?.object.userData.record?.id || null);
      this.onSelect(hit?.object.userData.record || null);
    });
    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(host);
    this.resize();
  }

  clear() {
    for (const child of [...this.group.children]) {
      child.traverse((item) => {
        item.geometry?.dispose();
        if (Array.isArray(item.material)) item.material.forEach((material) => material.dispose());
        else item.material?.dispose();
      });
      this.group.remove(child);
    }
    this.meshes = [];
    this.selected = null;
    this.render();
  }

  setModel(model, reframe = true) {
    this.clear();
    for (const record of model.objects || []) {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(record.vertices.flat(), 3));
      geometry.setIndex(record.faces.flat());
      geometry.computeVertexNormals();
      geometry.computeBoundingSphere();
      const material = new THREE.MeshStandardMaterial({ color: COLORS[record.role] || 0x90bbc9, roughness: .65, metalness: .08, side: THREE.DoubleSide, flatShading: true });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.userData.record = record;
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geometry, 20), new THREE.LineBasicMaterial({ color: 0x203b49, transparent: true, opacity: .8 }));
      edges.visible = this.edgesVisible;
      mesh.add(edges);
      this.group.add(mesh);
      this.meshes.push(mesh);
    }
    if (reframe && this.meshes.length) this.fit();
    this.render();
  }

  fit() {
    if (!this.meshes.length) return;
    const box = new THREE.Box3().setFromObject(this.group);
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    const maxSize = Math.max(size.x, size.y, size.z, .001);
    const halfAngle = this.camera.fov * Math.PI / 360;
    const distance = maxSize / (2 * Math.tan(halfAngle)) * Math.max(1, 1 / this.camera.aspect) * 1.6;
    this.camera.near = Math.max(maxSize / 10000, .00001);
    this.camera.far = Math.max(distance * 100, 100);
    this.camera.position.copy(center).add(new THREE.Vector3(1, -1.35, 1.05).normalize().multiplyScalar(distance));
    this.camera.updateProjectionMatrix();
    this.controls.target.copy(center);
    this.controls.minDistance = maxSize * .02;
    this.controls.maxDistance = distance * 10;
    this.controls.update();
    this.render();
  }

  select(id) {
    this.selected = id;
    for (const mesh of this.meshes) {
      const active = mesh.userData.record.id === id;
      mesh.material.emissive.setHex(active ? 0x254d4d : 0x000000);
      mesh.material.color.setHex(active ? 0xffd09a : (COLORS[mesh.userData.record.role] || 0x90bbc9));
    }
    this.render();
  }

  setEdges(visible) {
    this.edgesVisible = visible;
    for (const mesh of this.meshes) for (const child of mesh.children) child.visible = visible;
    this.render();
  }

  resize() {
    const width = Math.max(this.host.clientWidth, 1);
    const height = Math.max(this.host.clientHeight, 1);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
    this.render();
  }

  render() { this.renderer.render(this.scene, this.camera); }
  dispose() { this.observer.disconnect(); this.clear(); this.controls.dispose(); this.renderer.dispose(); this.renderer.domElement.remove(); }
}
