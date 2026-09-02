import { useEffect, useRef } from "react";
import { JEWEL_PALETTE, roundRect } from "./jewel";

type DayCount = { day: string; count: number };
type Props = { data: DayCount[]; height?: number };

const G = JEWEL_PALETTE.sapphire; // single-series jewel (blue) like the reference

/** Deals-this-week bar chart on a <canvas> with jewel gradient, top highlight,
 *  side shadow, shine streak + drop glow. Ported/simplified from the Gamified
 *  DealsThisWeekChart (single total per day, no per-agent stacking). */
export default function WeeklyBars3D({ data, height = 220 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const fontFamily = canvas.parentElement ? getComputedStyle(canvas.parentElement).fontFamily : "sans-serif";
    const cs = getComputedStyle(document.documentElement);
    const axisCol = cs.getPropertyValue("--text-faint").trim() || "#9CA3AF";
    const textCol = cs.getPropertyValue("--text").trim() || "#111827";
    const gridCol = "rgba(120,130,150,0.18)";
    const dpr = window.devicePixelRatio || 1;
    const totals = data.map((d) => d.count);
    let hov = -1;
    let bars: { x: number; y: number; w: number; h: number; i: number }[] = [];

    function draw() {
      if (!canvas || !ctx) return;
      const w = canvas.parentElement?.clientWidth ?? 600;
      const h = height;
      canvas.width = w * dpr; canvas.height = h * dpr;
      canvas.style.width = w + "px"; canvas.style.height = h + "px";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const pL = 34, pR = 8, pT = 22, pB = 30;
      const cw = w - pL - pR, ch = h - pT - pB;
      const mx = Math.max(...totals, 1) * 1.25;
      const slot = cw / Math.max(totals.length, 1);
      const bw = Math.min(slot * 0.5, 40);

      ctx.strokeStyle = gridCol; ctx.lineWidth = 0.5;
      ctx.fillStyle = axisCol; ctx.font = `500 10px ${fontFamily}`; ctx.textAlign = "right";
      for (let i = 0; i <= 4; i++) {
        const y = pT + (i / 4) * ch;
        ctx.beginPath(); ctx.moveTo(pL, y); ctx.lineTo(w - pR, y); ctx.stroke();
        ctx.fillText(String(Math.round(mx - (i / 4) * mx)), pL - 6, y + 3);
      }

      bars = [];
      data.forEach((d, i) => {
        const bh = (d.count / mx) * ch;
        const bx = pL + slot * i + (slot - bw) / 2;
        const by = pT + ch - bh;
        const bot = pT + ch;
        const isHov = i === hov;
        bars.push({ x: bx, y: by, w: bw, h: bh, i });

        if (d.count > 0) {
          const glow = ctx.createRadialGradient(bx + bw / 2, bot + 2, 2, bx + bw / 2, bot + 2, bw * (isHov ? 1.2 : 0.9));
          glow.addColorStop(0, G.glow); glow.addColorStop(1, "rgba(0,0,0,0)");
          ctx.fillStyle = glow;
          ctx.beginPath(); ctx.ellipse(bx + bw / 2, bot + 4, bw * (isHov ? 1.2 : 0.9), bw * 0.16, 0, 0, Math.PI * 2); ctx.fill();
        }

        if (bh > 0) {
          const grad = ctx.createLinearGradient(bx, by, bx, by + bh);
          grad.addColorStop(0, G.light); grad.addColorStop(0.7, G.mid); grad.addColorStop(1, G.deep);
          ctx.save();
          if (isHov) { ctx.shadowColor = G.glow; ctx.shadowBlur = 22; ctx.shadowOffsetY = 6; }
          ctx.fillStyle = grad;
          ctx.beginPath(); roundRect(ctx, bx, by, bw, bh, [5, 5, 0, 0]); ctx.fill();
          ctx.restore();

          // right-side cylinder shadow
          const sideShadow = ctx.createLinearGradient(bx, by, bx + bw, by);
          sideShadow.addColorStop(0, "rgba(0,0,0,0)"); sideShadow.addColorStop(0.55, "rgba(0,0,0,0)"); sideShadow.addColorStop(1, "rgba(0,0,0,0.35)");
          ctx.fillStyle = sideShadow; ctx.fillRect(bx, by, bw, bh);

          // top highlight
          const topHi = ctx.createLinearGradient(bx, by, bx, by + bh * 0.25);
          topHi.addColorStop(0, "rgba(255,255,255,0.4)"); topHi.addColorStop(1, "rgba(255,255,255,0)");
          ctx.fillStyle = topHi; ctx.fillRect(bx, by, bw, bh * 0.25);

          // shine streak
          if (bh >= 20) {
            const sx = bx + bw * 0.12, sw = bw * 0.18, shH = bh * 0.55;
            const sg = ctx.createLinearGradient(sx, by + 4, sx, by + 4 + shH);
            sg.addColorStop(0, "rgba(255,255,255,0.55)"); sg.addColorStop(1, "rgba(255,255,255,0)");
            ctx.fillStyle = sg; ctx.fillRect(sx, by + 4, sw, shH);
          }
        }

        ctx.fillStyle = textCol;
        ctx.font = `700 ${isHov ? 13 : 11}px ${fontFamily}`;
        ctx.textAlign = "center";
        if (d.count > 0) ctx.fillText(String(d.count), bx + bw / 2, by - 8);

        ctx.fillStyle = axisCol;
        ctx.font = `500 11px ${fontFamily}`;
        ctx.fillText(d.day, bx + bw / 2, bot + 16);
      });
    }
    draw();

    const onMove = (e: MouseEvent) => {
      const r = canvas.getBoundingClientRect();
      const x = e.clientX - r.left, y = e.clientY - r.top;
      let f = -1;
      bars.forEach((b, i) => { if (x >= b.x && x <= b.x + b.w && y >= b.y - 10 && y <= b.y + b.h) f = i; });
      if (f !== hov) { hov = f; canvas.style.cursor = f >= 0 ? "pointer" : "default"; draw(); }
    };
    const onLeave = () => { if (hov !== -1) { hov = -1; canvas.style.cursor = "default"; draw(); } };
    const onResize = () => draw();

    canvas.addEventListener("mousemove", onMove);
    canvas.addEventListener("mouseleave", onLeave);
    window.addEventListener("resize", onResize);
    return () => {
      canvas.removeEventListener("mousemove", onMove);
      canvas.removeEventListener("mouseleave", onLeave);
      window.removeEventListener("resize", onResize);
    };
  }, [data, height]);

  return <canvas ref={canvasRef} style={{ width: "100%", display: "block" }} aria-label="Deals this week — 3D bars" />;
}
