import os
import json
import logging
import time
from functools import wraps
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from dotenv import load_dotenv
import openai
from prompt_manager import prompt_manager

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global list to track LLM call timing data - accessed by app.py latency logger
llm_call_timings = []

def measure_llm_call(call_type: str):
    """Decorator to measure LLM API call duration (for sync functions)"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.perf_counter()
            
            try:
                result = func(*args, **kwargs)
                duration = (time.perf_counter() - start_time) * 1000
                
                llm_call_timings.append({
                    'type': call_type,
                    'duration': round(duration, 2),
                    'timestamp': time.time()
                })
                
                return result
            except Exception as e:
                duration = (time.perf_counter() - start_time) * 1000
                llm_call_timings.append({
                    'type': call_type,
                    'duration': round(duration, 2),
                    'error': str(e),
                    'timestamp': time.time()
                })
                raise
                
        return wrapper
    return decorator

class LLMProvider:
    def __init__(self):
        self.api_key = os.getenv('OPENAI_API_KEY')
        self.model = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
        self.base_url = os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')
        
        # PROMPT MANAGER ARCHITECTURE: Load system prompts via centralized PromptManager
        # Provides LLM with complete educational framework while maintaining clean separation
        # Story mode: Complete 10-step educational process with tutor personality
        # Facts mode: Engaging content creation guidelines for elementary students
        self.system_prompt = prompt_manager.get_story_system_prompt()
        self.fun_facts_system_prompt = prompt_manager.get_facts_system_prompt()
        
        # Initialize OpenAI client
        if self.api_key:
            self.client = openai.OpenAI(
                api_key=self.api_key,
                base_url=self.base_url
            )
            logger.info("OpenAI client initialized successfully")
        else:
            logger.warning("OpenAI API key not found. Using fallback responses.")
            self.client = None
        
    # REMOVED: _load_fun_facts_system_prompt() -> now handled by prompt_manager.get_facts_system_prompt()
    
    @measure_llm_call('story_generation')
    def generate_response(self, prompt: str, max_tokens: int = 300, system_prompt: str = None) -> str:
        """
        Generate a response using OpenAI API or fallback to sample responses
        """
        # Use provided system prompt or default to story system prompt
        effective_system_prompt = system_prompt if system_prompt is not None else self.system_prompt
        
        if self.client and self.api_key:
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": effective_system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=max_tokens,
                    temperature=0.7
                )

                print("---------START PROMPT----------")
                print(prompt)
                print("---------END PROMPT----------")
                
                response_text = response.choices[0].message.content.strip()

                print("---------START RESPONSE----------")
                print(response_text)
                print("---------END RESPONSE----------")

                return response_text
                
            except Exception as e:
                logger.error(f"OpenAI API error: {e}")
                return self._get_fallback_response(prompt)
        else:
            logger.info("Using fallback response (no OpenAI API key)")
            return self._get_fallback_response(prompt)
    
    def _get_fallback_response(self, prompt: str) -> str:
        """Fallback responses when OpenAI API is not available"""
        prompt_lower = prompt.lower()
        
        # Story responses
        if "story" in prompt_lower:
            if "space" in prompt_lower:
                return """🚀 Space adventures are incredible! Here's how our story begins:

Captain Zoe strapped herself into the **gleaming** spacecraft and checked all the controls. Her mission was to explore a mysterious new planet that scientists had **discovered** last week. As the rocket engines roared to life, she felt both nervous and excited about what **extraordinary** creatures she might encounter among the stars.

What happens next in our space adventure? Tell me what Captain Zoe sees or does when she reaches the mysterious planet!"""
            
            elif "fantasy" in prompt_lower:
                return """🏰 Fantasy quests are magical! Here's how our story begins:

Princess Maya ventured into the **enchanted** forest, carrying her grandmother's ancient map. The trees whispered secrets as she walked deeper into the woods, searching for the **legendary** Crystal of Courage. Strange **glimmers** of light danced between the branches, leading her toward an adventure she'd never forget.

What happens next in our fantasy quest? Tell me what Princess Maya discovers in the enchanted forest!"""
                
            else:
                return """Great choice! Let me start our story:

Our hero stepped into a new **adventure**, ready to face whatever challenges lay ahead. The **journey** would test their courage and **determination** in ways they never imagined.

What happens next in our story? Tell me how our hero begins their adventure!"""
        
        # Fact responses
        elif "fact" in prompt_lower:
            if "space" in prompt_lower:
                return """Jupiter is so **enormous** that more than 1,300 Earths could fit inside it! Scientists still **investigate** Jupiter to learn about its powerful storms, like the Great Red Spot — a giant spinning storm that's been raging for hundreds of years. Space missions help us **discover** new facts about our solar system every day! 🚀🪐✨"""
            
            elif "animal" in prompt_lower or "ocean" in prompt_lower:
                return """The blue whale is so **massive** that its heart alone weighs as much as a car! These **magnificent** creatures can **communicate** with each other across hundreds of miles using low-frequency sounds. Scientists still **study** these gentle giants to learn more about their amazing abilities! 🐋🌊💙"""
                
            else:
                return """Did you know that honey never spoils? Archaeologists have found **ancient** honey in Egyptian tombs that's over 3,000 years old and still perfectly good to eat! The **unique** properties of honey help it **preserve** itself naturally forever! 🍯✨🏺"""
        
        else:
            return "I'm here to help with stories and fun facts! What would you like to explore?"

    @measure_llm_call('vocabulary_question')
    def generate_vocabulary_question(self, word: str, context: str) -> Dict:
        """Generate vocabulary questions following Step 8 format"""
        if self.client and self.api_key:
            try:
                # FIXED: Use the already-selected vocabulary word from app.py (don't re-select)
                # The word parameter was already carefully selected using filtered available_words
                actual_word = word
                sentence_with_word = self._extract_sentence_with_word(actual_word, context)
                
                if not sentence_with_word:
                    sentence_with_word = context  # Fallback to full context
                
                # Load vocabulary question template from consolidated shared prompts
                from content_manager import content_manager
                
                shared_prompts = content_manager.content.get("shared_prompts", {})
                vocab_system = shared_prompts.get("vocabulary_system", {})
                question_generation = vocab_system.get("question_generation", {})
                prompt_template = question_generation.get("prompt_template", "")
                
                if not prompt_template:
                    # Fallback to hardcoded prompt if consolidated version not found
                    logger.warning("⚠️ Vocabulary question template not found in consolidated JSON, using fallback")
                    prompt_template = """Following Step 8 of the story process, create a vocabulary question for the word "{word}" from this sentence: "{sentence_context}"

Create the question in this exact format:
What does the word **{word}** mean?

Show the sentence where it was used: "{sentence_context}"

Then provide 4 multiple choice answers (a, b, c, d) with one correct answer and three distractors.
Make it appropriate for 2nd-3rd grade students.

Example format:
{{
  "question": "What does the word **courage** mean?\\n\\n\\"As Leo went forward, he felt a sense of **courage**.\\""
  "options": ["a) being scared", "b) being brave", "c) being tired", "d) being hungry"],
  "correctIndex": 1
}}

Return ONLY valid JSON with: question, options (array of 4 strings), correctIndex (0-3)"""
                
                # Format the prompt with the word and sentence context
                prompt = prompt_template.format(word=actual_word, sentence_context=sentence_with_word)

                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=200,
                    temperature=0.3
                )

                print(prompt)
                print("---------END PROMPT----------")
                
                result = json.loads(response.choices[0].message.content.strip())
                return result
                
            except Exception as e:
                logger.error(f"Error generating vocabulary question: {e}")
                return self._get_fallback_vocab_question(word, context)
        else:
            return self._get_fallback_vocab_question(word, context)

    def _get_fallback_vocab_question(self, word: str, context: str) -> Dict:
        """Fallback vocabulary questions with proper sentence extraction"""
        # FIXED: Use the already-selected vocabulary word from app.py (don't re-select)  
        # The word parameter was already carefully selected using filtered available_words
        actual_word = word
        sentence_with_word = self._extract_sentence_with_word(actual_word, context)
        
        if not sentence_with_word:
            sentence_with_word = context  # Fallback to full context
        
        vocab_questions = {
            "enormous": {
                "question": f'What does the word **enormous** mean?\n\n"{sentence_with_word}"',
                "options": ["a) very small", "b) very big", "c) very old", "d) very cold"],
                "correctIndex": 1
            },
            "investigate": {
                "question": f'What does the word **investigate** mean?\n\n"{sentence_with_word}"',
                "options": ["a) to ignore", "b) to find out about", "c) to run away", "d) to eat"],
                "correctIndex": 1
            },
            "discover": {
                "question": f'What does the word **discover** mean?\n\n"{sentence_with_word}"',
                "options": ["a) to lose something", "b) to hide something", "c) to find something new", "d) to break something"],
                "correctIndex": 2
            },
            "magnificent": {
                "question": f'What does the word **magnificent** mean?\n\n"{sentence_with_word}"',
                "options": ["a) very small", "b) very boring", "c) very beautiful", "d) very scary"],
                "correctIndex": 2
            },
            "ancient": {
                "question": f'What does the word **ancient** mean?\n\n"{sentence_with_word}"',
                "options": ["a) very new", "b) very old", "c) very fast", "d) very loud"],
                "correctIndex": 1
            },
            "gleaming": {
                "question": f'What does the word **gleaming** mean?\n\n"{sentence_with_word}"',
                "options": ["a) very dirty", "b) very shiny", "c) very broken", "d) very small"],
                "correctIndex": 1
            },
            "extraordinary": {
                "question": f'What does the word **extraordinary** mean?\n\n"{sentence_with_word}"',
                "options": ["a) very ordinary", "b) very boring", "c) very amazing", "d) very sad"],
                "correctIndex": 2
            },
            "enchanted": {
                "question": f'What does the word **enchanted** mean?\n\n"{sentence_with_word}"',
                "options": ["a) very scary", "b) very magical", "c) very dark", "d) very small"],
                "correctIndex": 1
            },
            "legendary": {
                "question": f'What does the word **legendary** mean?\n\n"{sentence_with_word}"',
                "options": ["a) very new", "b) very small", "c) very famous", "d) very quiet"],
                "correctIndex": 2
            },
            "massive": {
                "question": f'What does the word **massive** mean?\n\n"{sentence_with_word}"',
                "options": ["a) very tiny", "b) very huge", "c) very fast", "d) very quiet"],
                "correctIndex": 1
            },
            "communicate": {
                "question": f'What does the word **communicate** mean?\n\n"{sentence_with_word}"',
                "options": ["a) to be silent", "b) to talk or share", "c) to run away", "d) to eat food"],
                "correctIndex": 1
            },
            "courage": {
                "question": f'What does the word **courage** mean?\n\n"{sentence_with_word}"',
                "options": ["a) being scared", "b) being brave", "c) being tired", "d) being hungry"],
                "correctIndex": 1
            },
            "curious": {
                "question": f'What does the word **curious** mean?\n\n"{sentence_with_word}"',
                "options": ["a) wanting to know more", "b) feeling sleepy", "c) being angry", "d) being quiet"],
                "correctIndex": 0
            },
            "suspicious": {
                "question": f'What does the word **suspicious** mean?\n\n"{sentence_with_word}"',
                "options": ["a) very happy", "b) very loud", "c) seeming strange or wrong", "d) very bright"],
                "correctIndex": 2
            },
            "unique": {
                "question": f'What does the word **unique** mean?\n\n"{sentence_with_word}"',
                "options": ["a) very common", "b) one of a kind", "c) very old", "d) very small"],
                "correctIndex": 1
            },
            "preserve": {
                "question": f'What does the word **preserve** mean?\n\n"{sentence_with_word}"',
                "options": ["a) to throw away", "b) to keep safe", "c) to break", "d) to hide"],
                "correctIndex": 1
            },
            "study": {
                "question": f'What does the word **study** mean?\n\n"{sentence_with_word}"',
                "options": ["a) to ignore", "b) to learn about", "c) to forget", "d) to destroy"],
                "correctIndex": 1
            }
        }
        
        # Generate reasonable definitions for unknown words
        if actual_word.lower() not in vocab_questions:
            return {
                "question": f'What does the word **{actual_word}** mean?\n\n"{sentence_with_word}"',
                "options": [
                    f"a) something related to {actual_word}",
                    f"b) the opposite of {actual_word}",
                    f"c) a type of {actual_word}",
                    f"d) similar to {actual_word}"
                ],
                "correctIndex": 0
            }
        
        return vocab_questions.get(actual_word.lower())

    def _extract_sentence_with_word(self, word: str, context: str) -> Optional[str]:
        """
        Extract sentence containing the vocabulary word with improved matching
        Handles case-insensitive matching and punctuation variations
        """
        import re
        
        # Split on sentence-ending punctuation while preserving it
        sentences = re.split(r'([.!?])', context)
        
        # Reconstruct sentences properly
        for i in range(0, len(sentences) - 1, 2):
            if i + 1 < len(sentences):
                full_sentence = sentences[i] + sentences[i + 1]
                
                # Create a regex pattern that matches the word with case insensitivity and punctuation
                # This looks for **word** or **word,** or **word!** etc and handles case differences
                word_pattern = re.compile(
                    r'\*\*' + re.escape(word) + r'([,;:.!?]*)\*\*',
                    re.IGNORECASE
                )
                
                if word_pattern.search(full_sentence):
                    return full_sentence.strip()
        
        return None

    def extract_vocabulary_words(self, text: str) -> List[str]:
        """Extract vocabulary words from text (words between ** markers)"""
        import re
        words = re.findall(r'\*\*(.*?)\*\*', text)
        # Strip punctuation from extracted words for cleaner questions
        cleaned_words = []
        for word in words:
            cleaned_word = re.sub(r'[,;:.!?]+$', '', word.strip())
            if cleaned_word:  # Only add non-empty words
                cleaned_words.append(cleaned_word)
        return cleaned_words

    @measure_llm_call('grammar_feedback')
    def provide_grammar_feedback(self, user_text: str) -> Optional[str]:
        """Provide grammar feedback following Step 5 of the story process"""
        if self.client and self.api_key:
            try:
                # Load grammar feedback prompt from consolidated storywriting prompts
                from content_manager import content_manager
                
                storywriting_prompts = content_manager.content.get("storywriting_prompts", {})
                grammar_feedback = storywriting_prompts.get("grammar_feedback", {})
                prompt_template = grammar_feedback.get("prompt_template", "")
                
                if not prompt_template:
                    # Fallback to hardcoded prompt if consolidated version not found
                    logger.warning("⚠️ Grammar feedback prompt not found in consolidated JSON, using fallback")
                    prompt_template = """As a friendly English tutor for elementary students, analyze this text: "{user_text}"

Following Step 5 of the story process, if there are any grammatical errors or if this is an incomplete sentence, explain what a better written sentence would be. If there is better vocabulary to use, suggest it. 

IMPORTANT: Always include a specific example of a better sentence, especially for incomplete sentences.

If the grammar is correct and complete, return "CORRECT".
If there's a suggestion, provide it in this format: "You could make that sentence even better by saying '[improved version]'. [Brief explanation]."

Examples:
- For "practiced." → "You could make that sentence even better by saying 'They practiced hard for the next game.' This gives us more detail about what happened!"
- For "sara has curly hair" → "Great job, that's a nice start! You could add more details like 'Sara has curly hair and beautiful green eyes.' This makes the description more vivid!"
- For "He go to the store." → "You could make that sentence even better by saying 'He went to the store.' We use 'went' for past actions!"

Always provide encouraging feedback and specific examples to help young learners improve their writing."""
                
                # Format the prompt with the user text
                prompt = prompt_template.format(user_text=user_text)

                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    max_tokens=100,
                    temperature=0.3
                )

                print(prompt)
                print("---------END PROMPT----------")
                
                result = response.choices[0].message.content.strip()
                return None if result == "CORRECT" else result
                
            except Exception as e:
                logger.error(f"Error providing grammar feedback: {e}")
                return self._get_fallback_grammar_feedback(user_text)
        else:
            return self._get_fallback_grammar_feedback(user_text)

    def _get_fallback_grammar_feedback(self, user_text: str) -> Optional[str]:
        """Fallback grammar suggestions"""
        user_lower = user_text.lower().strip()
        
        # Check for incomplete sentences (single words or very short fragments)
        if len(user_text.split()) <= 2 and not user_text.endswith("!") and not user_text.endswith("?"):
            if user_lower == "practiced." or user_lower == "practiced":
                return "You could make that sentence even better by saying 'They practiced hard for the next game.' This gives us more detail about what happened!"
            elif user_lower in ["ran.", "ran", "walked.", "walked", "played.", "played"]:
                return f"You could make that sentence even better by saying 'They {user_lower.replace('.', '')} together.' This tells us more about what happened!"
            elif len(user_text.split()) == 1:
                return f"You could make that sentence even better by adding more details! Try something like 'They {user_text.replace('.', '')} hard to get better.'"
        
        # Check for descriptive writing opportunities
        if "has curly hair" in user_lower:
            return "Great job, that's a nice start! You could add more details like 'Sara has curly hair and beautiful green eyes.' This makes the description more vivid!"
            
        # Check for common grammar errors
        if user_lower.startswith("she find"):
            return "You could make that sentence even better by saying 'She discovers' or 'She finds'. The word 'discovers' sounds more exciting for an adventure story!"
        
        if "there" in user_lower and "alien" in user_lower:
            return "Nice addition! You could make that more descriptive by saying 'There was a strange alien' or 'A mysterious alien appeared'."
        
        if user_lower.startswith("he go"):
            return "You could make that sentence even better by saying 'He goes' or 'He went'. This makes the sentence sound more complete!"
        
        if user_lower.startswith("they go"):
            return "You could make that sentence even better by saying 'They go' or 'They went'. This makes the sentence sound more complete!"
        
        return None

    @measure_llm_call('api_status_check')
    def check_api_status(self) -> Dict[str, str]:
        """Check if OpenAI API is available"""
        if not self.api_key:
            return {"status": "missing_key", "message": "OpenAI API key not found"}
        
        if not self.client:
            return {"status": "not_initialized", "message": "OpenAI client not initialized"}
        
        try:
            # Test API connection
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "Hello"}],
                max_tokens=5
            )
            return {"status": "connected", "message": "OpenAI API is working"}
        except Exception as e:
            return {"status": "error", "message": f"API error: {str(e)}"}

# Global instance
llm_provider = LLMProvider()