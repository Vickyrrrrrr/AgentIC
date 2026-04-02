import { useState, useEffect } from 'react';
import { BookOpen, Activity, Cpu, Layers, GitMerge, Zap } from 'lucide-react';

const TypewriterText = ({ texts }: { texts: string[] }) => {
  const [textIndex, setTextIndex] = useState(0);
  const [text, setText] = useState('');
  const [isDeleting, setIsDeleting] = useState(false);

  useEffect(() => {
    const currentFullText = texts[textIndex];
    let typingSpeed = isDeleting ? 30 : 80;

    if (!isDeleting && text === currentFullText) {
      const timeoutId = setTimeout(() => setIsDeleting(true), 2000);
      return () => clearTimeout(timeoutId);
    } else if (isDeleting && text === '') {
      setIsDeleting(false);
      setTextIndex((prev) => (prev + 1) % texts.length);
      return;
    }

    const timeoutId = setTimeout(() => {
      setText(currentFullText.substring(0, text.length + (isDeleting ? -1 : 1)));
    }, typingSpeed);

    return () => clearTimeout(timeoutId);
  }, [text, isDeleting, textIndex, texts]);

  return (
    <span className="typewriter-text">{text}<span className="cursor">|</span></span>
  );
};

export const HomeComponent = ({ designsLength, setSelectedPage }: { designsLength: number, setSelectedPage: (page: string) => void }) => {
  return (
    <div className="home-interactive-wrapper">
      <div className="eda-background">
         <div className="eda-grid-lines"></div>
         <div className="eda-glow-orb"></div>
         <div className="eda-glow-orb secondary"></div>
      </div>

      <div className="home-content-z">
        <div className="hero-badge-modern">
           <span className="status-dot"></span> Core System Online
        </div>
        
        <h1 className="hero-title-modern">
          <TypewriterText texts={[
            "AgentIC Studio", 
            "Natural Language to GDSII", 
            "Autonomous Silicon", 
            "Electronic Design Automation"
          ]} />
        </h1>
        
        <p className="hero-desc-modern">
          Advanced Electronic Design Automation platform. Bridging natural language specification with physical silicon implementation using multi-agent collaborative intelligence.
        </p>

        <div className="action-row">
          <button className="btn-primary start-btn" onClick={() => setSelectedPage('Design Studio')}>
            <Activity size={16} /> Enter Design Studio
          </button>
          <button className="btn-secondary doc-btn" onClick={() => setSelectedPage('Documentation')}>
            <BookOpen size={16} /> Read Manual
          </button>
        </div>

        <div className="system-stats-modern">
          <div className="stat-card">
             <Cpu size={28} className="stat-icon" />
             <div className="stat-val">{designsLength}</div>
             <div className="stat-lbl">Active Designs</div>
          </div>
          <div className="stat-card">
             <Layers size={28} className="stat-icon" />
             <div className="stat-val">14</div>
             <div className="stat-lbl">Pipeline Stages</div>
          </div>
          <div className="stat-card">
             <GitMerge size={28} className="stat-icon" />
             <div className="stat-val">5</div>
             <div className="stat-lbl">Core Modules</div>
          </div>
          <div className="stat-card">
             <Zap size={28} className="stat-icon" />
             <div className="stat-val">Synced</div>
             <div className="stat-lbl">Agents</div>
          </div>
        </div>
      </div>
    </div>
  );
};
