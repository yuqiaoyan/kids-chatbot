from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel


class StorywritingPhase(str, Enum):
    """High-level phases of the storywriting conversation."""

    NOT_STARTED = "not_started"
    WRITING = "writing"
    DESIGNING = "designing"
    COMPLETED = "completed"
    VOCABULARY = "vocabulary"


class StoryMetadata(BaseModel):
    """Metadata about characters and locations introduced in the story (LEGACY)"""

    character_name: Optional[str] = None
    character_description: Optional[str] = None
    location_name: Optional[str] = None
    location_description: Optional[str] = None
    design_options: List[str] = []  # ["character", "location"] or subset
    needs_naming: bool = False  # True if entities are unnamed
    entity_descriptor: Optional[str] = None  # e.g., "the little boy", "the mysterious village"


class EntityLists(BaseModel):
    """Entity lists with explicit categorization from LLM"""

    named: List[str] = []  # Named entities (e.g., ["Alex", "Maya"])
    unnamed: List[str] = []  # Unnamed entities (e.g., ["the little boy", "clever inventor"])


class StoryEntities(BaseModel):
    """Complete entity structure from LLM response"""

    characters: EntityLists = EntityLists()
    locations: EntityLists = EntityLists()


class EnhancedStoryResponse(BaseModel):
    """New response format with explicit entity metadata"""

    story: str
    entities: StoryEntities
    vocabulary_words: List[str] = []


class StructuredStoryResponse(BaseModel):
    """Response format for story generation with metadata (LEGACY)"""

    story: str
    metadata: StoryMetadata


class DesignPrompt(BaseModel):
    """Design prompt information for frontend UI"""

    type: str  # "character" or "location"
    subject_name: str  # The character or location name
    aspect: str  # Current aspect being designed (e.g., "appearance", "personality")
    prompt_text: str  # "Help us bring Luna to life! They are very..."
    suggested_words: List[str]  # Max 8 vocabulary suggestions
    input_placeholder: str  # Custom placeholder text for input


class VocabularyPhase(BaseModel):
    questionsAsked: int = 0
    maxQuestions: int = 3
    isComplete: bool = False


class SessionData(BaseModel):
    topic: Optional[str] = None
    storyParts: List[str] = []
    currentStep: int = 0
    isComplete: bool = False
    conversationPhase: StorywritingPhase = StorywritingPhase.NOT_STARTED
    factsShown: int = 0
    currentFact: Optional[str] = None
    allFacts: List[str] = []
    askedVocabWords: List[str] = []  # Track vocabulary words that have been asked
    awaiting_story_confirmation: bool = False  # Track if waiting for user to confirm new story
    vocabularyPhase: VocabularyPhase = VocabularyPhase()  # Track vocabulary phase state
    contentVocabulary: List[str] = []  # Track vocabulary words used in generated content

    # Design Phase Fields
    designPhase: Optional[str] = None  # "character", "location", or None
    currentDesignAspect: Optional[str] = None  # Current aspect being designed
    designAspectHistory: List[str] = []  # Track used aspects to ensure rotation
    storyMetadata: Optional[StoryMetadata] = None  # Store LLM metadata about story elements (LEGACY)
    designComplete: bool = False  # Track if design phase is finished
    namingComplete: bool = False  # Track if naming phase is finished for unnamed entities

    # Enhanced Entity System Fields
    designedEntities: List[str] = []  # Track entities that have been designed
    currentEntityType: Optional[str] = None  # "character" or "location" for current entity
    currentEntityDescriptor: Optional[str] = None  # Descriptor of current entity being designed

    # Enhanced Story Structure Fields
    storyPhase: Optional[str] = None  # "setup", "development", "climax", "resolution"
    conflictType: Optional[str] = None  # "emotional", "social", "problem_solving", "environmental", "adventure"
    conflictScale: Optional[str] = None  # "epic_scale", "daily_scale"
    narrativeAssessment: Optional[dict] = None  # Last LLM assessment results
    characterGrowthScore: int = 0  # 0-100 scale tracking character development
    completenessScore: int = 0  # 0-100 scale tracking story completeness

    # Educational Logging Session Management Fields
    session_id: Optional[str] = None  # UUID for session tracking
    session_start: Optional[datetime] = None  # Session start timestamp
    last_activity: Optional[datetime] = None  # Last interaction timestamp for timeout
    turn_id: int = 0  # Sequential interaction counter within session
    current_story_id: Optional[str] = None  # Current story UUID
    current_funfact_id: Optional[str] = None  # Current fun fact UUID
    story_history: List[str] = []  # All story IDs in this session
    funfact_history: List[str] = []  # All fun fact IDs in this session


class ChatRequest(BaseModel):
    message: str
    mode: str = "storywriting"  # "storywriting" or "funfacts"
    sessionData: Optional[SessionData] = None
    storyMode: Optional[str] = "auto"  # "auto", "named", or "unnamed" for testing


class VocabQuestion(BaseModel):
    question: str
    options: List[str]
    correctIndex: int


class ChatResponse(BaseModel):
    response: str
    vocabQuestion: Optional[VocabQuestion] = None
    sessionData: Optional[SessionData] = None
    suggestedTheme: Optional[str] = None
    designPrompt: Optional[DesignPrompt] = None  # New field for design phase prompts
    educational_data: Optional[dict] = None  # Educational logging data (internal use)
    immediate_logging_performed: Optional[bool] = None
    latency_summary: Optional[dict] = None
