import { useEffect, useRef, useState } from "react";
import { createChart, AreaSeries, ColorType } from "lightweight-charts";
import { useFetchCardHistoryQuery } from "./catalogApi";

const GRADE_ORDER = ['ungraded', 'grade7', 'grade8', 'grade9', 'grade95', 'psa10', 'bgs10', 'cgc10', 'sgc10'];
const GRADE_LABEL: Record<string, string> = {
    ungraded: 'Ungraded', grade7: 'Grade 7', grade8: 'Grade 8', grade9: 'Grade 9',
    grade95: 'Grade 9.5', psa10: 'PSA 10', bgs10: 'BGS 10', cgc10: 'CGC 10', sgc10: 'SGC 10',
};

type Props = { game: string; id: number };

export default function PriceHistoryChart({ game, id }: Props) {
    const { data, isLoading } = useFetchCardHistoryQuery({ game, id });
    const containerRef = useRef<HTMLDivElement>(null);
    const [grade, setGrade] = useState('ungraded');

    const grades = data ? GRADE_ORDER.filter(g => data.series[g]?.length) : [];

    // default to the first available tier once data arrives
    useEffect(() => {
        if (grades.length && !grades.includes(grade)) setGrade(grades[0]);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [data]);

    useEffect(() => {
        const points = data?.series[grade];
        if (!containerRef.current || !points?.length) return;

        const chart = createChart(containerRef.current, {
            height: 340,
            autoSize: true,
            layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#333' },
            grid: { vertLines: { color: '#eee' }, horzLines: { color: '#eee' } },
            rightPriceScale: { borderColor: '#ddd' },
            timeScale: { borderColor: '#ddd' },
        });
        const series = chart.addSeries(AreaSeries, {
            lineColor: '#0176d5',
            topColor: 'rgba(1,118,213,0.35)',
            bottomColor: 'rgba(1,118,213,0.02)',
            lineWidth: 2,
            priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
        });
        series.setData(points.map(p => ({ time: p.date, value: p.price })));
        chart.timeScale().fitContent();

        return () => chart.remove();
    }, [data, grade]);

    if (isLoading) return <div>Loading chart…</div>;
    if (!grades.length) return <div className="est-note">No price history yet for this card.</div>;

    return (
        <div>
            <div className="grade-tabs">
                {grades.map(g => (
                    <button
                        key={g}
                        className={`btn btn--outline${g === grade ? ' btn--active' : ''}`}
                        onClick={() => setGrade(g)}
                    >
                        {GRADE_LABEL[g] ?? g}
                    </button>
                ))}
            </div>
            <div ref={containerRef} style={{ width: '100%' }} />
        </div>
    );
}
