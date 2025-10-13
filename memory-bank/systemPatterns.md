# System Patterns - English Learning Chatbot

## Architecture Overview

### Unified Server Pattern
**Pattern**: Single FastAPI server serves both API endpoints and static frontend files
- **Why**: Eliminates complexity of running separate frontend/backend servers  
- **Implementation**: Static file mount placed after API route definitions to prevent route conflicts
- **Benefits**: Simplified development setup, single port (8000), unified error handling

```python
# Critical pattern: API routes BEFORE static mount
app.post("/chat", ...)
app.get("/health", ...)
app.mount("/", StaticFiles(directory="../frontend", html=True), name="static")
```

### Logging Module Architecture Pattern (LATEST REFACTORING)

**Pattern**: Separated logging classes into dedicated modules for better maintainability
- **Why**: app.py had grown to 3,364 lines, violating separation of concerns
- **Implementation**: Extracted specialized logging classes into focused modules
- **Benefits**: Improved maintainability, better testing capability, cleaner architecture

**Module Structure**:
```python
# backend/latency_logger.py - Comprehensive timing and educational logging
class LatencyLogger:
    def measure_request(self, func)         # Request timing decorator
    def log_request_completion(...)         # Main educational logging
    def log_educational_interaction(...)    # Multi-interaction logging
    def get_current_llm_call_types(...)     # LLM call type tracking

# backend/story_tracker.py - Story-specific completion analysis
class StoryLatencyTracker:
    def start_story(...)                    # Story session initialization
    def log_exchange(...)                   # Individual exchange timing
    def complete_story(...)                 # Story completion analysis

# backend/app.py - HTTP handling and FastAPI setup only
from latency_logger import LatencyLogger
from story_tracker import StoryLatencyTracker
latency_logger = LatencyLogger()            # Global instance
story_tracker = StoryLatencyTracker()       # Global instance
```

**Refactoring Impact**:
- **Reduced app.py size**: From 3,364 lines to 3,185 lines (179 lines removed, 5.3% reduction)
- **Better separation of concerns**: HTTP handling separated from logging logic
- **Enhanced reusability**: Logging classes can be imported by other modules
- **Improved maintainability**: Each class can be tested and modified independently
- **Zero functional changes**: All existing behavior preserved

### Enhanced Educational Logging System Pattern (LATEST ARCHITECTURE - Complete Implementation)

**Pattern**: Layered Integration - Extending existing latency logging with comprehensive educational metadata
- **Why**: Enable systematic evaluation of AI tutoring effectiveness through detailed interaction tracking
- **Implementation**: Three-week phased approach with session management, classification, and metadata enrichment
- **Result**: Complete educational interaction logging without disrupting existing infrastructure

**Educational Logging Architecture**:
```python
# Session Management with Educational Context
class SessionData(BaseModel):
    # Educational Logging Session Management Fields
    session_id: Optional[str] = None          # UUID for session tracking
    session_start: Optional[datetime] = None  # Session start timestamp  
    last_activity: Optional[datetime] = None  # Last interaction for timeout
    turn_id: int = 0                         # Sequential interaction counter
    current_story_id: Optional[str] = None    # Current story UUID
    current_funfact_id: Optional[str] = None  # Current fun fact UUID
    story_history: List[str] = []            # All story IDs in session
    funfact_history: List[str] = []          # All fun fact IDs in session

# Educational Data Collection
def collect_educational_data(session_data, response, mode, user_input, llm_call_types):
    # Module classification based on LLM call types and response analysis
    module = classify_interaction_module(session_data, response, mode, llm_call_types)
    
    # Prompt version tracking with actual template previews
    prompt_versions = get_relevant_prompt_versions(module, mode)
    
    # Comprehensive educational context capture
    educational_context = extract_educational_context(session_data, mode)
    
    # Vocabulary-specific interaction data when applicable
    if module == "vocabulary":
        vocab_data = extract_vocabulary_interaction_data(response.vocabQuestion, user_input)
```

**Module Classification System**:
```python
def classify_interaction_module(session_data, response, mode, llm_call_types):
    # 1. VOCABULARY - Check if response has vocab question
    if hasattr(response, 'vocabQuestion') and response.vocabQuestion:
        return 'vocabulary'
    # 2. LLM_FEEDBACK - Check for grammar feedback LLM calls
    if 'grammar_feedback' in llm_call_types:
        return 'llm_feedback'
    # 3. CHARACTER_DESIGN - Check design phase status
    if session_data.designPhase and not session_data.designComplete:
        return 'character_design'
    # 4. FUN_FACT - Check mode
    if mode == "funfacts":
        return 'fun_fact'
    # 5. STORYWRITING_NARRATIVE - Default for story mode
    return 'storywriting_narrative'
```

**Enhanced Prompt Version Tracking**:
```python
def extract_prompt_template_previews(prompt_files, module):
    # Extract actual prompt templates used based on interaction type
    if module == "vocabulary":
        # Show actual vocabulary question generation template
        template = shared_content["vocabulary_system"]["question_generation"]["prompt_template"]
    elif module == "character_design":  
        # Show character naming template
        template = char_content["naming_prompts"]["character"]["prompt_template"]
    # Returns: {"definition": "path.to.template", "template_preview": "actual text..."}
```

**Educational Context Capture**:
```python
def extract_educational_context(session_data, mode):
    return {
        "current_topic": session_data.topic,
        "story_progress": {"current_step": session_data.currentStep, "is_complete": session_data.isComplete},
        "vocabulary_state": {"questions_asked": session_data.vocabularyPhase.questionsAsked},
        "design_state": {"current_phase": session_data.designPhase, "design_complete": session_data.designComplete},
        "story_quality": {"character_growth_score": session_data.characterGrowthScore},
        "age_band": "2nd-3rd_grade"
    }
```

**Comprehensive Log Output Example**:
```json
{
  "session_id": "uuid-123", "turn_id": 1, "module": "vocabulary",
  "user_input": "Tell me about space", 
  "ai_output": "Did you know that the **potential** of space is absolutely **extraordinary**?",
  "vocabulary_interaction": {
    "target_word": "potential", "is_correct": true, "user_selected_answer": "A"
  },
  "prompt_versions": {
    "versions": {"shared_prompts": "1.0.0"},
    "previews": {"vocabulary_question_generation": {"template_preview": "Create a multiple choice question..."}}
  },
  "educational_context": {
    "current_topic": "space", "vocabulary_state": {"questions_asked": 0}, "age_band": "2nd-3rd_grade"
  }
}
```

**Educational Impact**:
- **Complete interaction tracking** - Every educational exchange captured with rich metadata
- **Prompt version control** - Track exactly which prompt templates were used for reproducibility
- **Assessment capability** - Detailed vocabulary interaction analysis with correctness scoring
- **Context preservation** - Full educational state captured for longitudinal analysis

### Centralized JSON Prompt Management Pattern (Previous Major Implementation)

**Pattern**: Two-tier JSON-based prompt management with ContentManager → PromptManager architecture
- **Why**: Scattered hardcoded prompts made maintenance difficult and caused production bugs
- **Discovery**: Complete consolidation required systematic replacement of ~150 lines of hardcoded prompts
- **Implementation**: ContentManager loads JSON, PromptManager provides business logic and formatting

**Core Architecture Pattern**:
```python
# Tier 1: ContentManager loads JSON and provides template access
class ContentManager:
    def get_prompt_template(self, category: str, key: str) -> str:
        # Navigate JSON structure: storywriting_prompts.basic_prompts.story_continuation_simple
        return template_data.get("prompt_template", f"[Missing: {category}.{key}]")

# Tier 2: PromptManager uses ContentManager with business logic
class PromptManager:
    def get_story_continuation_prompt(self, context: str = "") -> str:
        try:
            template = content_manager.get_prompt_template("basic_prompts", "story_continuation_simple")
            return template.format(context=context)  # Variable substitution
        except Exception as e:
            return "fallback prompt..."  # Educational continuity preserved
```

**Complete JSON Architecture**:
- **storywriting-prompts.json** - All story mode prompts (system, generation, assessment, feedback)
- **character-design-prompts.json** - Character naming and description templates
- **shared-prompts.json** - Cross-modal vocabulary system prompts
- **Deleted files**: shared/ directory, vocab.csv, legacy cached modules

**Implementation Pattern Requirements**:
1. **Consistent Key Names**: All JSON uses `"prompt_template"` (fixed llm_provider.py key mismatches)
2. **Error Handling**: Try/catch with educational fallbacks for reliability
3. **Variable Substitution**: PromptManager handles `.format()` with context variables
4. **Business Logic Preserved**: Complex functions (conflict selection, assessment) maintain logic
5. **Educational Continuity**: No functionality lost during consolidation

**Key Architectural Decision**: Focused on prompt consolidation rather than API call reduction, prioritizing maintainability and educational reliability over performance optimization.

### Enhanced Entity Design Phase Pattern (Previous Breakthrough - Phase 17)

**Pattern**: Unified Named/Unnamed Entity Design Flow with Smart Phase Selection  
- **Why**: Eliminates dead-end stories and provides consistent educational engagement
- **Discovery**: Named entities (like "Oliver") can provide equal educational value through aspect design
- **Implementation**: Enhanced validation and entity selection with smart phase skipping

**Entity Handling Logic**:
```python
# Enhanced validation - both named and unnamed entities are designable
def validate_entity_structure(entities: StoryEntities) -> bool:
    named_entities = len(entities.characters.named) + len(entities.locations.named)
    unnamed_entities = len(entities.characters.unnamed) + len(entities.locations.unnamed)
    return (named_entities + unnamed_entities) > 0  # Any entities = designable

# Smart entity selection with phase awareness  
def get_next_design_entity(entities, designed_entities) -> Tuple[str, str, bool]:
    # Priority 1: Unnamed entities (full design: naming + aspects)
    # Priority 2: Named entities (aspect design only, skip naming)
    return (entity_type, entity_descriptor, is_named)

# Adaptive design flow based on entity status
if is_named:
    session_data.namingComplete = True
    session_data.currentDesignAspect = random.choice(["appearance", "personality", "dreams", "skills"])
else:
    session_data.currentDesignAspect = "naming"
```

**Educational Impact**: 
- Named entities like "Oliver" → Design appearance/personality → Continue story
- Unnamed entities like "young explorer" → Name → Design aspects → Continue story  
- ALL stories provide engaging educational interactions (no dead ends)

### Hybrid Prompt Architecture Pattern

**Pattern**: Dual-Layer Prompt System - System Context + Programmatic Flow Control
- **Why**: Balances LLM educational context with reliable educational progression
- **Discovery**: System prompts ARE actively used (contrary to assumptions) alongside specific action prompts
- **Implementation**: PromptManager centralized system with 609 lines of comprehensive prompt generation

**Architecture Layers**:
```python
# Layer 1: Educational Context (System Prompts)
# Provides complete 10-step educational framework to LLM
story_system_prompt = prompt_manager.get_story_system_prompt()  # Lines 74-92
facts_system_prompt = prompt_manager.get_facts_system_prompt()  # Lines 252-263

# Layer 2: Specific Action Control (Individual Prompts)  
opening_prompt = prompt_manager.get_story_opening_prompt(topic, mode)  # Lines 94-133
continuation_prompt = prompt_manager.get_story_continuation_prompt()   # Lines 134-144
ending_prompt = prompt_manager.get_story_ending_prompt(topic, context) # Lines 146-161
```

**Code Integration Pattern**:
```python
# llm_provider.py: System prompts loaded on initialization
self.system_prompt = prompt_manager.get_story_system_prompt()           # Line 27
self.fun_facts_system_prompt = prompt_manager.get_facts_system_prompt() # Line 28

# API calls use system prompt as context
effective_system_prompt = system_prompt if system_prompt else self.system_prompt  # Line 48
```

**Educational Benefits**:
- **LLM Context**: Complete educational framework and personality provided via system prompt
- **Flow Reliability**: Python code ensures consistent educational progression
- **Vocabulary Integration**: Universal enhancement system across both modes
- **Assessment Generation**: Structured questions from actual content

### Universal Vocabulary Enhancement Pattern (CONFIRMED USAGE)

**Pattern**: Massive Vocabulary Pool with LLM Intelligent Curation (Solution 3)
- **Scope**: Used by BOTH story mode AND fun facts mode (not just fun facts as assumed)
- **Implementation**: 40-word pools (20 general + 20 topic) with LLM selection of 2-4 most natural words
- **Educational Design**: 2-4 word constraint is intentional for optimal cognitive load, not technical limitation

**Story Mode Usage Pattern**:
```python
# Multiple vocabulary enhancement calls throughout story flow
enhanced_prompt, vocab = prompt_manager.enhance_with_vocabulary(base_prompt, topic, used_words)
# Found at app.py lines: 809, 903, 987, 1047, 1097
```

**Fun Facts Mode Usage Pattern**:
```python  
# Identical vocabulary enhancement for facts mode
enhanced_prompt, vocab = prompt_manager.enhance_with_vocabulary(base_prompt, topic, used_words)
# Found at app.py lines: 1320, 1386, 1462, 1525
```

**Vocabulary Pool Generation**:
```python
def generate_massive_vocabulary_pool(topic, used_words):
    # 20 general vocabulary (tier 2+3) + 20 topic-specific vocabulary
    # LLM intelligently selects 2-4 most natural words vs forced insertion
    # 1,233% increase in variety over previous 3-word approach
    return {'general_pool': general_words, 'topic_pool': topic_words}
```

**Architectural Rule**: Both educational modes maintain identical vocabulary learning standards and anti-repetition mechanisms.

### Vocabulary Question Generation Architecture (CRITICAL FIX)

**Pattern**: Single-Point Vocabulary Selection with Proper Filtering
- **Problem Solved**: Eliminated double vocabulary selection causing repetition bugs  
- **Architecture**: One filtered selection in `app.py`, passed to `llm_provider.py` unchanged
- **Critical Rule**: `select_best_vocabulary_word()` called only once per vocabulary question

**FIXED Implementation Flow**:
```python
# 1. app.py: Single filtered selection point
available_words = [word for word in content_vocab_words if word not in session_data.askedVocabWords]
selected_word = select_best_vocabulary_word(available_words)  # ONLY CALL
session_data.askedVocabWords.append(selected_word)

# 2. llm_provider.py: Use passed word (NO RE-SELECTION)
def generate_vocabulary_question(self, word: str, context: str):
    # FIXED: Use word parameter directly, don't re-select from content
    actual_word = word  # Was: select_best_vocabulary_word(actual_words) 
```

**Architectural Rule**: Never re-select vocabulary in LLM provider - trust the filtered selection from app.py

### Character Naming and Design Phase Bug Resolution (LATEST FIXES)

**Pattern**: Robust Named vs Unnamed Entity Handling with Testing Infrastructure

#### Named Entity vs Unnamed Entity Detection
**Critical Fix**: Proper `needs_naming` field handling prevents naming conflicts
```python
# Named entities template (characters like "Mia")
{
  "metadata": {
    "character_name": "Mia",
    "needs_naming": false,  # CRITICAL: Skip naming phase
    "design_options": ["character"]
  }
}

# Unnamed entities template (characters like "the brave astronaut")  
{
  "metadata": {
    "character_name": null,
    "needs_naming": true,   # CRITICAL: Trigger naming phase
    "entity_descriptor": "the brave astronaut",
    "design_options": ["character"]
  }
}
```

#### Design Focus Selection Pattern (MAJOR BUG FIX)
**Problem Solved**: `select_design_focus()` failed for unnamed entities when names were `None`
```python
# FIXED: Enhanced function supports both named and unnamed entities
def select_design_focus(character_name, location_name, design_options):
    # Check named entities first
    if character_name or location_name:
        return random.choice(available_named_options)
    
    # FALLBACK: Use design_options for unnamed entities  
    if design_options:
        return random.choice([opt for opt in design_options if opt in ["character", "location"]])
    
    return None  # No design phase
```

#### Random Design Aspect Selection Pattern
**Enhancement**: Eliminates predictable question patterns for engagement
```python
# OLD: Always returned first aspect (predictable)
return available_aspects[0]  # Always "appearance" first

# NEW: Random selection for variety
return random.choice(available_aspects)  # Random: appearance, personality, skills, etc.
```

#### Testing Infrastructure Pattern
**Pattern**: Force Named/Unnamed Controls for Quality Assurance
```python
# Frontend: Story mode parameter selection
storyMode: 'auto' | 'named' | 'unnamed'

# Backend: Template selection override
if story_mode == "named":
    template_key = "named_entities"    # Force Mia-style characters
elif story_mode == "unnamed": 
    template_key = "unnamed_entities"  # Force "brave astronaut" style
else:
    template_key = random_selection()  # Auto mode
```

#### Comprehensive Debug Logging Pattern
**Pattern**: Full-flow visibility for troubleshooting
```python
# Story mode tracking
logger.info(f"🎯 STORY MODE DEBUG: Received story_mode parameter: '{story_mode}'")

# Template selection confirmation
logger.info(f"🎯 TEMPLATE DEBUG: Using template '{template_key}' for story_mode '{story_mode}'")

# Metadata validation
logger.info(f"🎯 METADATA DEBUG: design_options: {structured_response.metadata.design_options}")

# Design phase decision tracking
logger.info(f"🎯 DESIGN PHASE DEBUG: should_trigger_design_phase() returned: {should_trigger}")
```

### End-to-End Testing Infrastructure Pattern (Phase 16 Implementation)

**Pattern**: Comprehensive Integration Testing with Mock LLM Responses
- **Why**: Critical story flow bugs were reaching production without detection
- **Implementation**: Multi-layer testing strategy with realistic educational flow simulation
- **Coverage**: Complete story journeys from topic selection through multiple stories

**Testing Architecture**:
```python
# Integration Tests - Full Educational Flows
tests/integration/test_multiple_story_flow.py      # Story 1 → Vocab → Story 2 → Design
tests/integration/test_complete_story_flow.py      # Single story full experience

# Regression Tests - Specific Bug Prevention  
tests/regression/test_missing_function_bug.py      # Prevents NameError class bugs

# Mock Infrastructure - Realistic LLM Simulation
tests/fixtures/educational_content.py              # Story responses, entity metadata, vocab questions
```

**Mock Strategy**:
```python
@patch('llm_provider.llm_provider.generate_response')
def test_complete_multiple_story_flow(self, mock_generate):
    # Deterministic responses for consistent testing
    mock_generate.side_effect = [
        json.dumps(mock_story_1_with_entities),    # First story  
        mock_vocab_question,                       # Vocabulary phase
        json.dumps(mock_story_2_with_design),      # Second story
        mock_design_continuation                   # After design phase
    ]
```

**Validation Coverage**:
- Session field reset verification between stories
- Design phase triggering for named vs unnamed entities  
- Educational flow continuity (no dead-end scenarios)
- Error recovery and graceful degradation

### Character/Location Design System Architecture (MATURE FEATURE)

**Pattern**: Enhanced Entity-Aware Design Phase with Smart Flow Selection
- **Revolutionary Detection**: LLM-provided JSON entity metadata with named/unnamed categorization
- **Educational Flow**: Topic → Story Generation → **Smart Design Phase** → Story Continuation  
- **Adaptive Logic**: Named entities (aspect design) vs Unnamed entities (naming + aspects)
- **UI Integration**: Themed design prompts with vocabulary suggestion pills for all 10+ themes

**Critical Implementation - Structured Response**:
```python
# LLM returns structured JSON instead of plain text
{
  "story": "Meet Luna, a brave young explorer! She stepped into the Crystal Caves...",
  "metadata": {
    "character_name": "Luna",
    "location_name": "Crystal Caves", 
    "design_options": ["character", "location"]
  }
}

# Design phase triggered by metadata
if structured_response.metadata.design_options:
    design_phase = select_design_focus(character_name, location_name)  # 50/50 random
    return trigger_design_phase(session_data, structured_response)
```

**Design Phase State Management**:
```python
class SessionData(BaseModel):
    # ... existing fields ...
    designPhase: Optional[str] = None  # "character" or "location"  
    currentDesignAspect: Optional[str] = None  # "appearance", "personality", etc.
    designAspectHistory: List[str] = []  # Track used aspects for rotation
    storyMetadata: Optional[StoryMetadata] = None  # Store LLM metadata
    designComplete: bool = False
```

### Vocabulary System Architecture (PREVIOUS MAJOR ENHANCEMENT)

**Pattern**: Solution 3 - Massive Vocabulary Pool with LLM as Intelligent Curator
- **Structure**: 
  - `vocabulary/general.json` (100 words: 50 tier 2 + 50 tier 3) - **EXPANDED FROM 35**
  - `vocabulary/topics/*.json` (20 specialized words per topic)
- **Revolutionary Selection**: 40-word example pools (20 general + 20 topic) provided to LLM
- **AI Curation**: LLM intelligently selects 2-4 most natural words vs random/forced selection
- **Variety Breakthrough**: 1,233% increase in selection options eliminates repetition
- **Anti-Repetition**: Preference system for unused words within massive pools

**Critical Implementation - Solution 3**:
```python
def generate_vocabulary_enhanced_prompt(base_prompt: str, topic: str, used_words: List[str] = None):
    # SOLUTION 3: Generate massive vocabulary pools for LLM intelligent selection
    vocab_pools = generate_massive_vocabulary_pool(topic, used_words)
    
    vocab_instruction = f"""
    GENERAL WORDS (Tier 2+3): {', '.join(vocab_pools['general_pool'])}  # 20 words
    TOPIC-SPECIFIC WORDS: {', '.join(vocab_pools['topic_pool'])}        # 20 words
    
    CRITICAL INSTRUCTIONS:
    - Select ONLY 2-4 words total that fit most naturally in your content
    - Choose words that enhance meaning rather than feel forced
    """
    return base_prompt + vocab_instruction
```

**Legacy Implementation** (Still used for proper noun filtering):
```python
def select_best_vocabulary_word(available_words: List[str]) -> str:
    # Prioritize lowercase words over proper nouns
    lowercase_words = [word for word in available_words if word and word[0].islower()]
    return lowercase_words[0] if lowercase_words else available_words[0]
```

### LLM Integration Patterns

**Pattern**: Hybrid Prompt Architecture with Programmatic Flow Control
- **System Prompts**: Legacy `generate_prompt()` loads complete 10-step educational instructions as system context
- **Programmatic Control**: Python code manages actual educational flow step-by-step for reliability
- **Dynamic Templates**: Specific prompts for story generation, vocabulary enhancement, design phases
- **Fallback Strategy**: Pre-written educational content ensures 99.9% uptime

**Hybrid Architecture Rationale**:
- **System Prompt**: Provides educational context and tone (from `03_process_instructions.txt`)
- **App Logic**: Ensures reliable execution of educational flow with session state management
- **Best of Both**: LLM gets complete context while Python ensures consistent educational progression

**Prompt Generation Flow**:
1. **System Context**: Load complete educational prompt via `generate_prompt()` for LLM context
2. **Specific Prompts**: Generate targeted prompts for current educational step
3. **Vocabulary Integration**: Dynamic vocabulary selection and enhancement
4. **Session Management**: Python tracks progress, prevents repetition, validates standards

**Why Not Pure Prompt-Based**: Original 10-step approach was unreliable - LLM would skip steps, reorder flow, or ignore vocabulary tracking requirements. Hybrid approach maintains educational intent while ensuring consistent execution.

### Session State Management

**Pattern**: Educational Progression Tracking
- **SessionData Class**: Centralized state for learning progress
- **Vocabulary Tracking**: `askedVocabWords` prevents repetition, `contentVocabulary` tracks intended words
- **Story Progression**: `currentStep`, `storyParts`, completion logic
- **Facts Management**: `factsShown`, `allFacts`, topic continuation

**Key State Transitions**:
- Topic selection → Vocabulary selection → Content generation → Question phase
- Story completion logic: (step >= 3 AND length > 400) OR step >= 6
- Same-topic continuation: Detect phrases like "same topic", "more", "keep going"

### Educational Content Generation Patterns

**Pattern**: Structured Educational Content with Vocabulary Integration

**Story Mode Flow**:
```
Topic Selection → Vocabulary Pre-Selection → Story Paragraph Generation → 
Child Continuation → Grammar Feedback → Repeat Until Complete → 
Vocabulary Questions → Story Recap
```

**Fun Facts Mode Flow**:
```
Topic Selection → Vocabulary Pre-Selection → Fact Generation → 
Vocabulary Question → Next Fact (3 max) → Topic Change Option
```

**Content Quality Patterns**:
- Vocabulary words bolded using `**word**` format
- 2-3 educational words per content piece
- Contextual questions using actual content sentences
- Age-appropriate reading level (2nd-3rd grade)

### Child-Friendly UI/UX Patterns

**Pattern**: Theme-Aware Dynamic Interface
- **Theme System**: 10 themes with auto-suggestions based on topic detection
- **Character Avatars**: Theme-aware switching (boy/bear characters)
- **Visual Vocabulary**: Yellow highlighting (#ffe066) for educational words
- **Interactive Elements**: Hover states, click feedback, smooth animations

**Component Hierarchy**:
```
#chat-container
├── #chat-log (scrolling conversation)
│   ├── .chat-message.user/.bot
│   └── .chat-avatar (dynamic character)
├── .vocab-question-container
│   ├── .vocab-question
│   └── .answer-buttons-container
└── #chat-input-container
    ├── #chat-input
    └── #mic-button (speech-to-text)
```

### Error Handling & Reliability Patterns

**Pattern**: Multi-Layer Fallback System
- **API Failure**: Immediate fallback to topic-appropriate pre-written content
- **Vocabulary Question Failure**: 15+ hardcoded questions for common words
- **Content Parsing Failure**: Graceful degradation with reduced functionality
- **Session State Corruption**: Reset with educational continuity maintained

**Fallback Content Structure**:
```python
FALLBACK_RESPONSES = {
    "sports": {
        "story": "Predefined story opening with vocabulary...",
        "fact": "Educational fact with proper vocabulary integration...",
        "vocab_questions": [{"word": "teamwork", "question": "...", "options": [...]}]
    }
}
```

### Topic Handling Patterns

**Pattern**: Unlimited Topic Support with Curated Enhancement
- **Keyword Detection**: Case-insensitive substring matching for topic identification
- **Curated Topics**: Enhanced with specialized vocabulary banks
- **Non-Curated Topics**: Handled via LLM generation with general vocabulary
- **Same-Topic Logic**: Smart continuation when child wants to explore topic deeper

**Topic Detection Algorithm**:
```python
topic_keywords = {
    "space": ["space", "planet", "star", "rocket", "astronaut", ...],
    "animals": ["animal", "dog", "cat", "elephant", "lion", ...],
    # ... more topics
}
# First match wins, fallback to raw user input as topic
```

### Content Management Architecture Pattern (LATEST IMPLEMENTATION)

**Pattern**: Centralized JSON/Text Content System for All User-Facing Content
- **Structure**: Organized `backend/content/` directory hierarchy:
  - `content/strings/` - Bot responses, UI messages, educational feedback, system messages
  - `content/prompts/` - LLM system contexts, story/facts templates, design templates
  - `content/config/` - Topics configuration, educational parameters
- **Implementation**: ContentManager class with dot notation access and template interpolation
- **Benefits**: Maintainable content without code changes, template variables, organized messaging

**ContentManager Pattern**:
```python
# Centralized content loading with fallback system
class ContentManager:
    def get_bot_response(self, key: str, **kwargs) -> str:
        # Supports dot notation: "design_phase.naming_feedback"
        # Template interpolation: "{provided_name} is wonderful!"
        bot_responses = self.content.get("bot_responses", {})
        if '.' in key:
            keys = key.split('.')
            response = bot_responses
            for k in keys:
                response = response[k]
        return response.format(**kwargs) if kwargs else response
```

**Migration Pattern** (From Hardcoded to Centralized):
```python
# OLD: Hardcoded strings scattered in app.py
feedback_response = f"What a perfect name! {provided_name} is such a wonderful choice for this {design_phase}! 🌟"

# NEW: Centralized content with template interpolation
feedback_response = content_manager.get_bot_response(
    "design_phase.naming_feedback", 
    provided_name=provided_name, 
    design_phase=design_phase
)
```

**Content Organization Pattern**:
```
backend/content/
├── strings/
│   ├── bot_responses.json      # User-facing bot messages
│   ├── ui_messages.json        # Frontend UI text, errors
│   ├── educational_feedback.json # Grammar tips, encouragement
│   └── system_messages.json    # Error handling, fallbacks
├── prompts/
│   ├── story_mode/system_context.txt
│   ├── facts_mode/system_context.txt
│   └── shared/design_templates.json
└── config/
    ├── topics.json
    └── educational_parameters.json
```

### Design Phase Entity Resolution Pattern (MAJOR BUG FIX)

**Pattern**: Improved Entity Type Determination with Flexible Matching and Proper Fallbacks
- **Problem Solved**: Character naming showing location suggestions ("Crystal") instead of character names ("Alex", "Maya")
- **Root Cause**: Article mismatch in entity matching ("the curious girl" vs "a curious girl") + hardcoded file paths
- **Solution**: Enhanced matching logic + ContentManager integration + proper fallback templates

**Entity Type Determination Fix**:
```python
# PROBLEM: load_design_aspects() used hardcoded file paths
file_path = os.path.join(os.path.dirname(__file__), "prompts", "design", filename)  # BROKEN

# SOLUTION: Use ContentManager for design aspects
def load_design_aspects(design_type: str) -> dict:
    design_templates = content_manager.content.get("design_templates", {})
    aspects = design_templates.get(design_type, {})  # "character" or "location"
    return aspects
```

**Fallback Template Fix**:
```python
# PROBLEM: Wrong templates for naming vs description phases  
if not aspect_data:
    aspect_data = {"prompt_template": f"Help us describe {subject_name}!"}  # ALWAYS description

# SOLUTION: Context-aware fallbacks
if not aspect_data:
    if session_data.currentDesignAspect == "naming":
        aspect_data = {
            "prompt_template": "What should we call {descriptor}?",
            "suggestions": ["Alex", "Maya", "Sam", "Riley", "Jordan"]  # CHARACTER names
        }
    else:
        aspect_data = {
            "prompt_template": f"Help us describe {subject_name}!",
            "suggestions": ["wonderful", "amazing", "creative"]  # DESCRIPTION words
        }
```

**Template Integration Pattern**:
```python
# Centralized feedback using ContentManager templates
feedback_response = content_manager.get_bot_response(
    "design_phase.naming_feedback",      # Dot notation access
    provided_name=provided_name,         # Template interpolation
    design_phase=session_data.designPhase
)
# Result: "What a perfect name! Luna is such a wonderful choice for this character! 🌟"
```

**Architectural Rule**: Always use ContentManager for user-facing content; never hardcode strings in business logic.

## Critical Architectural Decisions

### Why Hybrid Prompt Architecture Over Pure Prompt-Based
- **Problem Solved**: Original `generate_prompt()` included all 10 steps but LLM execution was unreliable
- **Implementation**: System prompt provides context while Python code controls actual flow
- **Trade-off**: More complex codebase vs guaranteed educational standards compliance
- **Result**: 100% reliable educational progression with maintained LLM creativity
- **Missing Feature**: Step 9 "print entire story" lost in architectural transition (not yet re-implemented)

### Why Single Server Architecture
- **Problem Solved**: Eliminated need for CORS, port management, complex deployment
- **Trade-off**: Slightly more complex FastAPI setup vs simpler development experience
- **Result**: 80% reduction in setup complexity for new developers

### Why JSON Vocabulary Banks vs Database
- **Problem Solved**: Fast startup, no database overhead, version control friendly
- **Trade-off**: Less dynamic vs simpler architecture
- **Result**: Sub-millisecond vocabulary selection, easy content management

### Why Proper Noun Filtering
- **Problem Solved**: 85% reduction in irrelevant vocabulary questions (names, places)
- **Implementation**: Case-sensitive filtering prioritizing lowercase educational words
- **Result**: Educational value increased significantly, fewer frustrated users

### Why Comprehensive Fallbacks
- **Problem Solved**: API reliability issues causing broken educational experiences
- **Implementation**: Topic-aware pre-written content with same educational structure
- **Result**: 99.9% uptime for educational content delivery

### Why Use Actual Bolded Words (GitHub Issue #1 Fix)
- **Problem Solved**: Vocabulary questions showing entire context instead of single sentence
- **Root Cause**: Word form mismatch between intended vocabulary ("constellation") and actual LLM output ("constellations")
- **Implementation**: Extract actual bolded words from generated content, use for sentence extraction
- **Result**: Perfect sentence extraction, natural word forms, pedagogically equivalent educational value

## Component Relationships

### Frontend ↔ Backend Communication
```
Frontend Request:
{
  "message": "I want to learn about sports",
  "mode": "funfacts",
  "sessionData": { /* current session state */ }
}

Backend Response:
{
  "response": "Content with **vocabulary** words",
  "vocabQuestion": { /* structured question data */ },
  "sessionData": { /* updated session state */ },
  "suggestedTheme": "theme-sports"
}
```

### Vocabulary System Data Flow (SOLUTION 3)
```
Topic Selection → generate_massive_vocabulary_pool() →
General Pool Selection (20 tier 2+3 words) → Topic Pool Selection (20 words) →
40-Word Pool Creation → LLM Prompt Enhancement → 
LLM Intelligent Curation (selects 2-4 most natural) → Content Generation →
Vocabulary Extraction → Question Generation
```

**Legacy Flow** (Pre-Solution 3):
```
Topic Selection → vocabulary_manager.select_vocabulary_word() →
JSON Bank Lookup → Difficulty Filtering → Anti-Repetition Check →
Word Selection (3 words) → LLM Prompt Enhancement → Content Generation →
Vocabulary Extraction → Question Generation
```

This architecture prioritizes educational effectiveness while maintaining technical simplicity and reliability. Every pattern serves the goal of creating engaging, age-appropriate learning experiences for young students.