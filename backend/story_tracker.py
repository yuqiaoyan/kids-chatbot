"""
Story Tracker Module

Track latency specifically for story exchanges and completion analysis.
This module provides story-specific timing instrumentation and completion analysis.
"""

import time
import json
import os
import statistics
import logging


class StoryLatencyTracker:
    """Track latency specifically for story exchanges and completion"""
    
    def __init__(self):
        self.story_exchanges = []
        self.story_start_time = None
        
    def start_story(self, topic: str, mode: str):
        """Initialize story tracking"""
        self.story_start_time = time.time()
        self.story_exchanges = []
        
    def log_exchange(self, exchange_type: str, latency: float, user_input: str, response_type: str):
        """Log individual story exchange timing"""
        exchange_data = {
            'exchange_number': len(self.story_exchanges) + 1,
            'exchange_type': exchange_type,  # 'story_continuation', 'vocab_question', 'design_phase'
            'latency': round(latency, 2),
            'response_type': response_type,
            'timestamp': time.time(),
            'user_input_length': len(user_input) if user_input else 0
        }
        
        self.story_exchanges.append(exchange_data)
        
    def complete_story(self, topic: str, mode: str):
        """Log story completion summary with latency analysis"""
        if not self.story_exchanges:
            return None
            
        total_story_time = time.time() - self.story_start_time if self.story_start_time else 0
        average_latency = sum(ex['latency'] for ex in self.story_exchanges) / len(self.story_exchanges)
        latencies = [ex['latency'] for ex in self.story_exchanges]
        
        story_summary = {
            'story_completion_time': time.time(),
            'topic': topic,
            'mode': mode,
            'total_exchanges': len(self.story_exchanges),
            'average_latency': round(average_latency, 2),
            'total_story_duration': round(total_story_time, 2),
            'exchanges': self.story_exchanges,
            'latency_distribution': {
                'min': min(latencies),
                'max': max(latencies),
                'median': round(statistics.median(latencies), 2)
            }
        }
        
        # Log to dedicated story latency file
        os.makedirs('logs', exist_ok=True)
        with open('logs/story_latency.jsonl', 'a') as f:
            f.write(json.dumps(story_summary) + '\n')
        
        # Reset for next story
        self.story_exchanges = []
        self.story_start_time = None
        
        return story_summary