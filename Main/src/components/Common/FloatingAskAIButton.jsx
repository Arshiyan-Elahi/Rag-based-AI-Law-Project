import React from 'react';
import { Bot } from 'lucide-react';
import './FloatingAskAIButton.css';

export default function FloatingAskAIButton() {
  const handleClick = () => {
    window.open('http://65.21.244.158:8085/', '_blank');
  };

  return (
    <button className="floating-ask-ai-btn" onClick={handleClick} aria-label="Ask AI">
      <Bot size={20} />
      <span>Ask AI</span>
    </button>
  );
}
