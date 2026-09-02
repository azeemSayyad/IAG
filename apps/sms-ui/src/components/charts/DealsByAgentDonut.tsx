import { useEffect, useRef, useState } from "react";
import { stopsFor } from "./jewel";

type AgentBreakdown = { name: string; deals: number; color: string };
type Props = { breakdown: AgentBreakdown[] };
type Tooltip = { x: number; y: number; agent: string; deals: number; color: string };

/** Tilted 3D donut on a <canvas> with depth layers, jewel radial gradients,
 *  hover lift + tooltip + intro animation. Ported from the Gamified dashboard
 *  (DealsByAgentDonut). No deps. */
export default function DealsByAgentDonut({ breakdown }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [tooltip, setTooltip] = useState<Tooltip | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || breakdown.length === 0) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const fontFamily = canvas.parentElement ? getComputedStyle(canvas.parentElement).fontFamily : "sans-serif";
    const centerCol = getComputedStyle(document.documentElement).getPropertyValue("--text-strong").trim() || "#1A1A1A";
    // Secondary label colour: read the theme's muted text so it flips in dark mode
    // (was a hardcoded mid-grey that went muddy/invisible on the dark canvas).
    const mutedCol = getComputedStyle(document.documentElement).getPropertyValue("--text-muted").trim() || "#6B6B6B";
    const data = breakdown.map((b) => ({ ...b, dark: stopsFor(b.color).deep }));
    const sumDeals = data.reduce((s, d) => s + d.deals, 0);
    const total = sumDeals || 1;
    const dpr = window.devicePixelRatio || 1;
    const s = 280;

    canvas.width = s * dpr; canvas.height = s * dpr;
    canvas.style.width = s + "px"; canvas.style.height = s + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const rx = 118, ry = 108, rri = 70, depth = 20;
    const cx = s / 2, cy = s / 2 - 6;
    let anim = 0, hov = -1;

    function segs(p: number) {
      let a = -Math.PI / 2;
      const totA = Math.PI * 2 * p;
      return data.map((b) => {
        const ang = (b.deals / total) * totA;
        const o = { b, a0: a, a1: a + ang };
        a += ang;
        return o;
      });
    }

    function draw() {
      if (!ctx) return;
      ctx.clearRect(0, 0, s, s);
      const sg = segs(anim);

      for (let layer = depth; layer > 0; layer -= 0.5) {
        sg.forEach((seg, i) => {
          const grow = i === hov ? 8 : 0;
          ctx.beginPath();
          ctx.ellipse(cx, cy + layer, rx + grow, ry + grow, 0, seg.a0, seg.a1);
          ctx.ellipse(cx, cy + layer, rri, ry * (rri / rx), 0, seg.a1, seg.a0, true);
          ctx.closePath();
          ctx.fillStyle = seg.b.dark;
          ctx.globalAlpha = i === hov ? 0.4 : 0.28;
          ctx.fill();
          ctx.globalAlpha = 1;
        });
      }

      sg.forEach((seg, i) => {
        const offY = i === hov ? -3 : 0;
        const grow = i === hov ? 8 : 0;
        // Clamp the inter-slice gap to the slice's own width. Without this, a very
        // thin slice (a small agent when the total is large) would have a0 > a1 after
        // trimming, and ctx.ellipse then draws the LONG way round — a near-full ring in
        // that agent's colour, painting the whole donut one colour (it broke as soon as
        // any agent's share fell below ~0.3%). Keeps thin slivers thin but correct.
        const gap = Math.min(0.01, (seg.a1 - seg.a0) * 0.35);
        const a0 = seg.a0 + gap;
        const a1 = seg.a1 - gap;
        ctx.beginPath();
        ctx.ellipse(cx, cy + offY, rx + grow, ry + grow, 0, a0, a1);
        ctx.ellipse(cx, cy + offY, rri, ry * (rri / rx), 0, a1, a0, true);
        ctx.closePath();
        const stops = stopsFor(seg.b.color);
        const wedgeGrad = ctx.createRadialGradient(cx, cy + offY, rri * 0.6, cx, cy + offY, rx + grow);
        wedgeGrad.addColorStop(0, stops.light);
        wedgeGrad.addColorStop(0.6, stops.mid);
        wedgeGrad.addColorStop(1, stops.deep);
        ctx.fillStyle = wedgeGrad;
        ctx.fill();

        ctx.save();
        ctx.lineWidth = 2.2;
        ctx.strokeStyle = seg.b.color;
        ctx.globalAlpha = 0.55;
        ctx.beginPath(); ctx.ellipse(cx, cy + offY, rx + grow, ry + grow, 0, a0, a1); ctx.stroke();
        ctx.beginPath(); ctx.ellipse(cx, cy + offY, rri, ry * (rri / rx), 0, a0, a1); ctx.stroke();
        ctx.restore();

        ctx.save();
        ctx.clip();
        const lx = cx - rx * 0.45, ly = cy + offY - ry * 0.55;
        const rad = ctx.createRadialGradient(lx, ly, 2, lx, ly, rx * 1.1);
        rad.addColorStop(0, "rgba(255,255,255,0.7)");
        rad.addColorStop(0.28, "rgba(255,255,255,0.18)");
        rad.addColorStop(0.6, "rgba(255,255,255,0)");
        ctx.fillStyle = rad;
        ctx.fillRect(cx - rx, cy + offY - ry, rx * 2, ry * 2);
        const sh = ctx.createRadialGradient(cx, cy + offY + ry * 0.6, rri * 0.4, cx, cy + offY + ry * 0.6, rx * 1.1);
        sh.addColorStop(0, "rgba(0,0,0,0)");
        sh.addColorStop(1, "rgba(0,0,0,0.18)");
        ctx.fillStyle = sh;
        ctx.fillRect(cx - rx, cy + offY - ry, rx * 2, ry * 2);
        ctx.restore();

        ctx.strokeStyle = i === hov ? "rgba(255,255,255,0.7)" : "rgba(255,255,255,0.18)";
        ctx.lineWidth = i === hov ? 1.2 : 0.4;
        ctx.stroke();
      });

      // Agent name + count drawn ON each wedge (so the donut reads on its own).
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      const lrx = (rri + rx) / 2;
      const lry = (ry * (rri / rx) + ry) / 2;
      sg.forEach((seg, i) => {
        if (seg.a1 - seg.a0 < 0.34) return; // wedge too thin to label legibly
        const mid = (seg.a0 + seg.a1) / 2;
        const offY = i === hov ? -3 : 0;
        const x = cx + Math.cos(mid) * lrx;
        const y = cy + offY + Math.sin(mid) * lry;
        const first = String(seg.b.name).split(/\s+/)[0];
        ctx.save();
        ctx.shadowColor = "rgba(0,0,0,0.55)";
        ctx.shadowBlur = 3;
        ctx.fillStyle = "#fff";
        ctx.font = `700 11px ${fontFamily}`;
        ctx.fillText(first, x, y - 5);
        ctx.font = `600 10px ${fontFamily}`;
        ctx.fillText(String(seg.b.deals), x, y + 7);
        ctx.restore();
      });

      ctx.fillStyle = centerCol;
      ctx.font = `700 24px ${fontFamily}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      if (hov >= 0) {
        ctx.fillText(String(data[hov].deals), cx, cy - 2);
        ctx.fillStyle = mutedCol;
        ctx.font = `500 10px ${fontFamily}`;
        ctx.fillText(data[hov].name, cx, cy + 16);
      } else {
        // Center total = sum of EVERY agent's deals shown on the ring (incl. the
        // thin, unlabeled slivers), so the number always reconciles with the slices.
        ctx.fillText(String(Math.round(sumDeals * anim)), cx, cy - 2);
        ctx.fillStyle = mutedCol;
        ctx.font = `500 10px ${fontFamily}`;
        ctx.fillText("deals", cx, cy + 16);
      }
    }

    let raf: number | null = null;
    const startTime = performance.now();
    function intro(n: number) {
      const p = Math.min((n - startTime) / 900, 1);
      anim = 1 - Math.pow(1 - p, 3);
      draw();
      raf = p < 1 ? requestAnimationFrame(intro) : null;
    }
    raf = requestAnimationFrame(intro);

    function hit(mx: number, my: number) {
      const dx = (mx - cx) / rx, dy = (my - cy) / ry;
      const d = Math.sqrt(dx * dx + dy * dy);
      if (d < rri / rx || d > 1) return -1;
      const ang = Math.atan2(my - cy, mx - cx);
      const sgs = segs(1);
      for (let i = 0; i < sgs.length; i++) {
        const a0 = sgs[i].a0, a1 = sgs[i].a1;
        let a = ang;
        while (a < a0) a += Math.PI * 2;
        while (a > a0 + Math.PI * 2) a -= Math.PI * 2;
        if (a >= a0 && a <= a1) return i;
      }
      return -1;
    }
    const onMove = (e: MouseEvent) => {
      const r = canvas.getBoundingClientRect();
      const cssX = e.clientX - r.left, cssY = e.clientY - r.top;
      const h = hit(cssX, cssY);
      if (h !== hov) { hov = h; canvas.style.cursor = h >= 0 ? "pointer" : "default"; draw(); }
      if (h >= 0) { const seg = data[h]; setTooltip({ x: cssX, y: cssY, agent: seg.name, deals: seg.deals, color: seg.color }); }
      else setTooltip(null);
    };
    const onLeave = () => { if (hov !== -1) { hov = -1; canvas.style.cursor = "default"; draw(); } setTooltip(null); };

    canvas.addEventListener("mousemove", onMove);
    canvas.addEventListener("mouseleave", onLeave);
    return () => {
      canvas.removeEventListener("mousemove", onMove);
      canvas.removeEventListener("mouseleave", onLeave);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [breakdown]);

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 14 }}>
      <div style={{ position: "relative" }}>
        <canvas ref={canvasRef} aria-label="Deals by agent — 3D donut" />
        {tooltip && (
          // Flip the tooltip leftward on right-side wedges so the agent name
          // never overflows the panel edge (where it got clipped / hidden under
          // the next panel). z-index keeps it above sibling panels.
          <div
            className="sd-tooltip"
            style={{
              left: tooltip.x + (tooltip.x > 140 ? -14 : 14),
              top: Math.max(4, tooltip.y - 38),
              transform: tooltip.x > 140 ? "translateX(-100%)" : undefined,
              zIndex: 50,
            }}
          >
            <span className="sd-tooltip-dot" style={{ background: tooltip.color }} />
            <span className="sd-tooltip-name">{tooltip.agent}</span>
            <span className="sd-tooltip-val">{tooltip.deals} deals</span>
          </div>
        )}
      </div>
    </div>
  );
}
