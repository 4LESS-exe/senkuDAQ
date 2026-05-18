import { useEffect, useRef } from 'react';
import uPlot from 'uplot';
import 'uplot/dist/uPlot.min.css';

function hLinesPlugin(lines) {
  return {
    hooks: {
      draw: (u) => {
        const ctx = u.ctx;
        const { left, width } = u.bbox;

        lines.forEach(({ valor, color, dash, label }) => {
          if (valor === null || valor === undefined) return;

          const y = Math.round(u.valToPos(valor, 'y', true));

          ctx.save();
          ctx.beginPath();
          ctx.strokeStyle = color;
          ctx.lineWidth = 1.5;
          ctx.setLineDash(dash || []);
          ctx.moveTo(left, y);
          ctx.lineTo(left + width, y);
          ctx.stroke();

          if (label) {
            ctx.fillStyle = color;
            ctx.font = '10px Courier New, monospace';
            ctx.textAlign = 'right';
            ctx.fillText(label, left + width - 4, y - 4);
          }

          ctx.restore();
        });
      }
    }
  };
}

export default function UplotChart({ data, height = 300, umbralIgnN = null, umbralApgN = null, rangoMaxN = null }) {
  const containerRef = useRef(null);
  const uplotInst = useRef(null);

  const buildPlotData = (data, umbral) => {
    const xs = data[0];
    const ys = data[1];
    const verdes = umbral !== null
      ? ys.map(v => (v >= umbral ? v : null))
      : ys.map(() => null);
    return [xs, ys, verdes];
  };

  useEffect(() => {
    if (!containerRef.current) return;

    const opts = {
      width: containerRef.current.clientWidth || 600,
      height,
      scales: {
        x: { time: false },
        y: {
          // Rango mínimo garantizado = rangoMaxN; si los datos lo superan, se expande
          range: (u, dataMin, dataMax) => {
            const techo = rangoMaxN ?? 10;
            return [
              Math.min(dataMin ?? 0, 0),
              Math.max(dataMax ?? techo, techo),
            ];
          }
        }
      },
      axes: [
        { stroke: "#b0b0b0", grid: { stroke: "#dddddd" } },
        { stroke: "#b0b0b0", grid: { stroke: "#dddddd" } }
      ],
      series: [
        {},
        { label: "Empuje (N)", stroke: "#c0392b", width: 2 },
        { label: "Activo",     stroke: "#1e8449", width: 2.5, spanGaps: false }
      ],
      plugins: [
        hLinesPlugin([
          { valor: umbralIgnN, color: "#e67e22", dash: [6, 3], label: `Ign. ${umbralIgnN?.toFixed(2)} N` },
          { valor: umbralApgN, color: "#7f8c8d", dash: [3, 3], label: `Apg. ${umbralApgN?.toFixed(2)} N` },
        ])
      ],
    };

    uplotInst.current = new uPlot(opts, [[0], [0], [null]], containerRef.current);

    const ro = new ResizeObserver(entries => {
      const w = entries[0].contentRect.width;
      if (uplotInst.current && w > 0) {
        uplotInst.current.setSize({ width: w, height });
      }
    });
    ro.observe(containerRef.current);

    return () => {
      ro.disconnect();
      uplotInst.current?.destroy();
      uplotInst.current = null;
    };
  }, [height, umbralIgnN, umbralApgN, rangoMaxN]);

  useEffect(() => {
    if (uplotInst.current && data.length >= 2 && data[0].length > 0) {
      uplotInst.current.setData(buildPlotData(data, umbralIgnN));
    }
  }, [data, umbralIgnN]);

  return (
    <div
      ref={containerRef}
      className="w-full bg-white rounded shadow-sm border border-gray-300"
    />
  );
}