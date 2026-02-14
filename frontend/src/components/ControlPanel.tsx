import React, { useState } from 'react';
import { sendBotCommand } from '../services/api';

const ControlPanel: React.FC = () => {
  const [loading, setLoading] = useState(false);

  const handleCommand = async (action: string) => {
    try {
      setLoading(true);
      await sendBotCommand(action);
      alert(`Command '${action}' sent successfully!`);
    } catch (error) {
      console.error('Error sending command:', error);
      alert(`Failed to send command '${action}'`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="card">
      <h3 className="text-xl font-bold mb-4">Bot Controls</h3>
      
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <button
          onClick={() => handleCommand('start')}
          disabled={loading}
          className="px-4 py-3 bg-green-600 hover:bg-green-700 disabled:bg-gray-600 text-white font-semibold rounded-lg transition-colors"
        >
          ▶️ Start
        </button>
        
        <button
          onClick={() => handleCommand('pause')}
          disabled={loading}
          className="px-4 py-3 bg-yellow-600 hover:bg-yellow-700 disabled:bg-gray-600 text-white font-semibold rounded-lg transition-colors"
        >
          ⏸️ Pause
        </button>
        
        <button
          onClick={() => handleCommand('resume')}
          disabled={loading}
          className="px-4 py-3 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600 text-white font-semibold rounded-lg transition-colors"
        >
          ⏯️ Resume
        </button>
        
        <button
          onClick={() => handleCommand('stop')}
          disabled={loading}
          className="px-4 py-3 bg-red-600 hover:bg-red-700 disabled:bg-gray-600 text-white font-semibold rounded-lg transition-colors"
        >
          ⏹️ Stop
        </button>
      </div>

      <div className="mt-6 p-4 bg-slate-700/50 rounded-lg">
        <h4 className="font-semibold mb-2">⚠️ Important Notes:</h4>
        <ul className="text-sm text-slate-300 space-y-1">
          <li>• <strong>Start:</strong> Begin trading operations</li>
          <li>• <strong>Pause:</strong> Temporarily halt new positions (keep existing ones)</li>
          <li>• <strong>Resume:</strong> Continue trading after pause</li>
          <li>• <strong>Stop:</strong> Stop bot and close all positions</li>
        </ul>
      </div>
    </div>
  );
};

export default ControlPanel;
