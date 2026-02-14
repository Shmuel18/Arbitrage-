import React, { useState, useEffect } from 'react';
import { Line } from 'react-chartjs-2';
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler,
} from 'chart.js';
import { getPnL } from '../services/api';

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
  Filler
);

const PnLChart: React.FC = () => {
  const [timeRange, setTimeRange] = useState(24);
  const [chartData, setChartData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchPnLData();
  }, [timeRange]);

  const fetchPnLData = async () => {
    try {
      const data = await getPnL(timeRange);
      
      if (!data.data_points || data.data_points.length === 0) {
        setChartData(null);
        setLoading(false);
        return;
      }

      const labels = data.data_points.map((point: any) => 
        new Date(point.timestamp * 1000).toLocaleTimeString()
      );
      
      const pnlData = data.data_points.map((point: any) => point.cumulative_pnl);

      setChartData({
        labels,
        datasets: [
          {
            label: 'Cumulative P&L',
            data: pnlData,
            borderColor: 'rgb(147, 51, 234)',
            backgroundColor: 'rgba(147, 51, 234, 0.1)',
            fill: true,
            tension: 0.4,
          },
        ],
      });
      setLoading(false);
    } catch (error) {
      console.error('Error fetching P&L data:', error);
      setLoading(false);
    }
  };

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        display: true,
        labels: {
          color: '#e2e8f0',
        },
      },
      title: {
        display: true,
        text: 'P&L Over Time',
        color: '#e2e8f0',
        font: {
          size: 18,
        },
      },
    },
    scales: {
      y: {
        ticks: {
          color: '#94a3b8',
          callback: function(value: any) {
            return '$' + value.toFixed(2);
          },
        },
        grid: {
          color: 'rgba(148, 163, 184, 0.1)',
        },
      },
      x: {
        ticks: {
          color: '#94a3b8',
        },
        grid: {
          color: 'rgba(148, 163, 184, 0.1)',
        },
      },
    },
  };

  return (
    <div className="card">
      <div className="flex justify-between items-center mb-4">
        <h3 className="text-xl font-bold">Performance Chart</h3>
        <div className="flex space-x-2">
          <button
            onClick={() => setTimeRange(1)}
            className={`px-3 py-1 rounded text-sm ${
              timeRange === 1 ? 'bg-purple-600' : 'bg-slate-700 hover:bg-slate-600'
            }`}
          >
            1h
          </button>
          <button
            onClick={() => setTimeRange(24)}
            className={`px-3 py-1 rounded text-sm ${
              timeRange === 24 ? 'bg-purple-600' : 'bg-slate-700 hover:bg-slate-600'
            }`}
          >
            24h
          </button>
          <button
            onClick={() => setTimeRange(168)}
            className={`px-3 py-1 rounded text-sm ${
              timeRange === 168 ? 'bg-purple-600' : 'bg-slate-700 hover:bg-slate-600'
            }`}
          >
            7d
          </button>
        </div>
      </div>

      <div className="h-96">
        {loading ? (
          <div className="flex items-center justify-center h-full text-slate-400">
            Loading chart...
          </div>
        ) : chartData ? (
          <Line data={chartData} options={options} />
        ) : (
          <div className="flex items-center justify-center h-full text-slate-400">
            No data available
          </div>
        )}
      </div>
    </div>
  );
};

export default PnLChart;
