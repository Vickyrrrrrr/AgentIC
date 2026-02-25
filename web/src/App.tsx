import { useState, useEffect, Suspense } from 'react';
import axios from 'axios';
import { Canvas } from '@react-three/fiber';
import { Chip3D } from './components/Chip3D';
import { Dashboard } from './pages/Dashboard';
import { DesignStudio } from './pages/DesignStudio';
import { Benchmarking } from './pages/Benchmarking';
import { Fabrication } from './pages/Fabrication';
import './index.css';

const App = () => {
  const [selectedPage, setSelectedPage] = useState('Design Studio');
  const [designs, setDesigns] = useState<{ name: string, has_gds: boolean }[]>([]);
  const [selectedDesign, setSelectedDesign] = useState<string>('');

  useEffect(() => {
    const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
    axios.get(`${API_BASE_URL}/designs`)
      .then(res => {
        const data = res.data.designs;
        setDesigns(data);
        if (data.length > 0) {
          const withGds = data.find((d: any) => d.has_gds);
          setSelectedDesign(withGds ? withGds.name : data[0].name);
        }
      })
      .catch(err => console.error("Failed to fetch designs", err));
  }, []);

  return (
    <div className="app-container">
      {/* Sidebar Navigation */}
      <nav className="sidebar">
        <h2>AgentIC</h2>

        {/* Global Design Selector */}
        <div style={{ padding: '0 1rem', marginTop: '10px' }}>
          <p style={{ fontSize: '12px', color: '#888', marginBottom: '5px', fontFamily: 'Fira Code' }}>ACTIVE CHIP</p>
          <select
            value={selectedDesign}
            onChange={(e) => setSelectedDesign(e.target.value)}
            style={{ width: '100%', background: '#111', color: '#00FF88', border: '1px solid #333', padding: '8px', borderRadius: '4px', fontFamily: 'Fira Code', outline: 'none' }}
          >
            {designs.map(d => (
              <option key={d.name} value={d.name}>
                {d.name} {d.has_gds ? '[GDS✓]' : ''}
              </option>
            ))}
          </select>
        </div>

        <div style={{ marginTop: '20px', display: 'flex', flexDirection: 'column' }}>
          {['Home', 'Dashboard', 'Design Studio', 'Benchmarking', 'Fabrication'].map(page => (
            <button
              key={page}
              className={selectedPage === page ? 'active' : ''}
              onClick={() => setSelectedPage(page)}
            >
              <span style={{ marginRight: '10px' }}>
                {page === 'Home' && '🏠'}
                {page === 'Dashboard' && '📊'}
                {page === 'Design Studio' && '⚡'}
                {page === 'Benchmarking' && '📈'}
                {page === 'Fabrication' && '🏗️'}
              </span>
              {page}
            </button>
          ))}
        </div>

        <div style={{ marginTop: 'auto', textAlign: 'center', color: '#555', fontSize: '12px' }}>
          <p>AgentIC Web App v2.0</p>
          <p>© 2026</p>
        </div>
      </nav>

      {/* Main Content Area */}
      <main className="main-content">
        {selectedPage === 'Home' && (
          <div className="landing-container">
            <h1 className="landing-title">AgentIC</h1>
            <p className="landing-subtitle">Autonomous Silicon Design Framework</p>
            <div className="chip-canvas-container">
              <Suspense fallback={<div style={{ color: '#00FF88' }}>Loading 3D Engine...</div>}>
                <Canvas camera={{ position: [0, 4, 6], fov: 45 }}>
                  {/* Note: ambientLight intrinsic elements exist natively in R3F */}
                  <ambientLight intensity={0.5} />
                  <pointLight position={[10, 10, 10]} intensity={1} />
                  <Chip3D />
                </Canvas>
              </Suspense>
            </div>

            <button
              className="btn-primary"
              style={{ marginTop: '30px', fontSize: '1.2rem' }}
              onClick={() => setSelectedPage('Design Studio')}
            >
              Start New Project
            </button>
          </div>
        )}

        {selectedPage === 'Dashboard' && <Dashboard selectedDesign={selectedDesign} />}
        {selectedPage === 'Design Studio' && <DesignStudio />}
        {selectedPage === 'Benchmarking' && <Benchmarking selectedDesign={selectedDesign} />}
        {selectedPage === 'Fabrication' && <Fabrication selectedDesign={selectedDesign} hasGds={designs.find(d => d.name === selectedDesign)?.has_gds} />}
      </main>
    </div>
  );
};

export default App;
