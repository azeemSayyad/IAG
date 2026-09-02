import { useEffect, useRef } from "react";
import { JEWEL_PALETTE, roundRect } from "./jewel";

type Performer = { name: string; members: number };
type Props = { data: Performer[]; height?: number };

// Per-gem light/mid/deep stops → true gradient + glow on each 3D bar.
const PALETTES = [
  { base: JEWEL_PALETTE.sapphire.mid, light: JEWEL_PALETTE.sapphire.light, dark: JEWEL_PALETTE.sapphire.deep, glow: JEWEL_PALETTE.sapphire.glow, shine: "rgba(255,255,255,0.55)" },
  { base: JEWEL_PALETTE.topaz.mid, light: JEWEL_PALETTE.topaz.light, dark: JEWEL_PALETTE.topaz.deep, glow: JEWEL_PALETTE.topaz.glow, shine: "rgba(255,255,255,0.55)" },
  { base: JEWEL_PALETTE.emerald.mid, light: JEWEL_PALETTE.emerald.light, dark: JEWEL_PALETTE.emerald.deep, glow: JEWEL_PALETTE.emerald.glow, shine: "rgba(255,255,255,0.55)" },
  { base: JEWEL_PALETTE.rose.mid, light: JEWEL_PALETTE.rose.light, dark: JEWEL_PALETTE.rose.deep, glow: JEWEL_PALETTE.rose.glow, shine: "rgba(255,255,255,0.55)" },
  { base: JEWEL_PALETTE.onyx.mid, light: JEWEL_PALETTE.onyx.light, dark: JEWEL_PALETTE.onyx.deep, glow: JEWEL_PALETTE.onyx.glow, shine: "rgba(255,255,255,0.45)" },
];

/** Isometric 3D bar chart on a <canvas> — ported from the Gamified dashboard
 *  (TopPerformersChart). Hover glow + tooltip + intro animation. No deps. */
export default function TopPerformers3D({ data, height }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || data.length === 0) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const fontFamily = canvas.parentElement ? getComputedStyle(canvas.parentElement).fontFamily : "sans-serif";
    const textCol = getComputedStyle(document.documentElement).getPropertyValue("--text").trim() || "rgba(60,60,60,0.8)";
    const dpr = window.devicePixelRatio || 1;
    const maxVal = Math.max(...data.map((d) => d.members), 1) * 1.15;

    let W = 0, H = 0, floorY = 0, horizon = 0, sceneL = 0, sceneR = 0, slotW = 0, barW = 0, depth3d = 0, lift3d = 0, maxBarH = 0;
    let hovered = -1;
    const animVals = data.map((d) => ({ cur: d.members, target: d.members }));
    let animFrame: number | null = null;
    let tooltipAlpha = 0, tooltipTarget = 0;

    function layout() {
      if (!canvas) return;
      W = canvas.parentElement?.clientWidth || 620;
      H = height ?? Math.max(260, Math.round(W * 0.52));
      canvas.style.width = W + "px";
      canvas.style.height = H + "px";
      canvas.width = W * dpr;
      canvas.height = H * dpr;
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
      floorY = H * 0.84;
      horizon = H * 0.06;
      sceneL = W * 0.04;
      sceneR = W * 0.96;
      slotW = (sceneR - sceneL) / data.length;
      barW = slotW * 0.6;
      depth3d = barW * 0.28;
      lift3d = depth3d * 0.5;
      maxBarH = (floorY - horizon) * 0.92;
    }
    const barBaseX = (i: number) => sceneL + slotW * i + (slotW - barW) / 2;
    const barHeightPx = (val: number) => (val / maxVal) * maxBarH;

    function draw() {
      if (!ctx) return;
      ctx.clearRect(0, 0, W, H);
      data.forEach((d, i) => {
        const p = PALETTES[i % PALETTES.length];
        const isHov = hovered === i;
        const bx = barBaseX(i);
        const bh = barHeightPx(animVals[i].cur);
        const by = floorY - bh;
        const bot = floorY;

        const glowR = ctx.createRadialGradient(bx + barW / 2 + depth3d / 2, bot + 2, 2, bx + barW / 2 + depth3d / 2, bot + 2, barW * (isHov ? 1.2 : 0.9));
        glowR.addColorStop(0, p.glow);
        glowR.addColorStop(1, "rgba(0,0,0,0)");
        ctx.fillStyle = glowR;
        ctx.beginPath();
        ctx.ellipse(bx + barW / 2 + depth3d / 2, bot + 4, barW * (isHov ? 1.2 : 0.9), barW * 0.16, 0, 0, Math.PI * 2);
        ctx.fill();

        ctx.save();
        if (isHov) { ctx.shadowColor = p.glow.replace(/[\d.]+\)$/, "0.45)"); ctx.shadowBlur = 26; ctx.shadowOffsetY = 4; }
        const faceGrad = ctx.createLinearGradient(bx, by, bx + barW, by);
        faceGrad.addColorStop(0, p.light); faceGrad.addColorStop(0.4, p.base); faceGrad.addColorStop(1, p.dark);
        ctx.fillStyle = faceGrad;
        ctx.beginPath();
        roundRect(ctx, bx, by, barW, bh, [5, 5, 0, 0]);
        ctx.fill();
        ctx.restore();

        const sideGrad = ctx.createLinearGradient(bx + barW, by, bx + barW + depth3d, by);
        sideGrad.addColorStop(0, p.dark); sideGrad.addColorStop(1, "rgba(0,0,0,0.55)");
        ctx.fillStyle = sideGrad;
        ctx.beginPath();
        ctx.moveTo(bx + barW, by); ctx.lineTo(bx + barW + depth3d, by - lift3d); ctx.lineTo(bx + barW + depth3d, bot - lift3d); ctx.lineTo(bx + barW, bot); ctx.closePath();
        ctx.fill();

        const topGrad = ctx.createLinearGradient(bx, by, bx + barW + depth3d, by - lift3d);
        topGrad.addColorStop(0, p.light); topGrad.addColorStop(0.5, p.base); topGrad.addColorStop(1, p.dark);
        ctx.fillStyle = topGrad;
        ctx.beginPath();
        ctx.moveTo(bx, by); ctx.lineTo(bx + depth3d, by - lift3d); ctx.lineTo(bx + barW + depth3d, by - lift3d); ctx.lineTo(bx + barW, by); ctx.closePath();
        ctx.fill();

        ctx.fillStyle = isHov ? "rgba(255,255,255,0.18)" : "rgba(255,255,255,0.10)";
        ctx.beginPath();
        ctx.moveTo(bx, by); ctx.lineTo(bx + depth3d, by - lift3d); ctx.lineTo(bx + barW + depth3d, by - lift3d); ctx.lineTo(bx + barW, by); ctx.closePath();
        ctx.fill();

        const shineGrad = ctx.createLinearGradient(bx + 4, by, bx + barW * 0.35, by + bh);
        shineGrad.addColorStop(0, p.shine); shineGrad.addColorStop(0.4, "rgba(255,255,255,0.06)"); shineGrad.addColorStop(1, "rgba(255,255,255,0)");
        ctx.fillStyle = shineGrad;
        ctx.beginPath();
        roundRect(ctx, bx + 4, by + 4, barW * 0.26, Math.max(0, bh - 8), [3, 3, 0, 0]);
        ctx.fill();

        ctx.strokeStyle = "rgba(255,255,255,0.3)";
        ctx.lineWidth = 0.75;
        ctx.beginPath(); ctx.moveTo(bx, by); ctx.lineTo(bx, bot); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(bx, by); ctx.lineTo(bx + depth3d, by - lift3d); ctx.stroke();

        const staticBy = floorY - barHeightPx(d.members);
        ctx.font = `600 ${isHov ? 13 : 11}px ${fontFamily}`;
        ctx.fillStyle = isHov ? p.dark : textCol;
        ctx.textAlign = "center";
        ctx.fillText(`${d.members}`, bx + barW / 2 + depth3d / 2, staticBy - lift3d - (isHov ? 13 : 8));

        ctx.font = `500 12px ${fontFamily}`;
        ctx.fillStyle = isHov ? p.base : textCol;
        const nm = d.name.length > 11 ? d.name.slice(0, 10) + "…" : d.name;
        ctx.fillText(nm, bx + barW / 2, floorY + 20);
      });

      if (hovered >= 0 && tooltipAlpha > 0.01) {
        const i = hovered;
        const p = PALETTES[i % PALETTES.length];
        const bx = barBaseX(i);
        const staticBy = floorY - barHeightPx(data[i].members);
        const tx = bx + barW / 2 + depth3d / 2;
        const ty = staticBy - lift3d - 38;
        const tw = 160, th = 30, tr = 7;
        const lx = Math.max(tw / 2 + 8, Math.min(W - tw / 2 - 8, tx));
        ctx.save();
        ctx.globalAlpha = tooltipAlpha;
        ctx.fillStyle = p.dark;
        ctx.beginPath(); roundRect(ctx, lx - tw / 2, ty - th / 2, tw, th, tr); ctx.fill();
        ctx.fillStyle = "rgba(255,255,255,0.95)";
        ctx.font = `500 12px ${fontFamily}`;
        ctx.textAlign = "center";
        ctx.fillText(`${data[i].name} · ${data[i].members} members`, lx, ty + 4);
        ctx.fillStyle = p.dark;
        ctx.beginPath(); ctx.moveTo(lx - 6, ty + th / 2); ctx.lineTo(lx + 6, ty + th / 2); ctx.lineTo(lx, ty + th / 2 + 7); ctx.closePath(); ctx.fill();
        ctx.restore();
      }
    }

    function animate() {
      let busy = false;
      animVals.forEach((av) => {
        const diff = av.target - av.cur;
        if (Math.abs(diff) > 0.5) { av.cur += diff * 0.15; busy = true; } else av.cur = av.target;
      });
      const td = tooltipTarget - tooltipAlpha;
      if (Math.abs(td) > 0.01) { tooltipAlpha += td * 0.2; busy = true; } else tooltipAlpha = tooltipTarget;
      draw();
      animFrame = busy ? requestAnimationFrame(animate) : null;
    }
    function startAnim() { if (!animFrame) animFrame = requestAnimationFrame(animate); }

    function getHovered(mx: number, my: number) {
      for (let i = 0; i < data.length; i++) {
        const bx = barBaseX(i);
        const by = floorY - barHeightPx(animVals[i].cur);
        if (mx >= bx && mx <= bx + barW + depth3d && my >= by - lift3d && my <= floorY) return i;
      }
      return -1;
    }
    const onMove = (e: MouseEvent) => {
      const r = canvas.getBoundingClientRect();
      const h = getHovered(e.clientX - r.left, e.clientY - r.top);
      if (h !== hovered) {
        if (hovered >= 0) animVals[hovered].target = data[hovered].members;
        hovered = h;
        if (hovered >= 0) { animVals[hovered].target = data[hovered].members * 1.06; tooltipTarget = 1; canvas.style.cursor = "pointer"; }
        else { tooltipTarget = 0; canvas.style.cursor = "default"; }
        startAnim();
      }
    };
    const onLeave = () => {
      if (hovered >= 0) animVals[hovered].target = data[hovered].members;
      hovered = -1; tooltipTarget = 0; canvas.style.cursor = "default"; startAnim();
    };
    const onResize = () => { layout(); draw(); };

    canvas.addEventListener("mousemove", onMove);
    canvas.addEventListener("mouseleave", onLeave);
    window.addEventListener("resize", onResize);

    layout();
    animVals.forEach((av) => (av.cur = 0));
    startAnim();

    return () => {
      canvas.removeEventListener("mousemove", onMove);
      canvas.removeEventListener("mouseleave", onLeave);
      window.removeEventListener("resize", onResize);
      if (animFrame) cancelAnimationFrame(animFrame);
    };
  }, [data, height]);

  return <canvas ref={canvasRef} style={{ width: "100%", display: "block" }} aria-label="Members per agent — 3D bars" />;
}
