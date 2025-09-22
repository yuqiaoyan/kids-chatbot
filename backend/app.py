from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import Dict, List, Optional, Tuple
import logging
import json
import os
import time
from datetime import datetime
from functools import wraps
from llm_provider import llm_provider
# REMOVED: generate_prompt import no longer needed (PromptManager handles all prompt logic)
from vocabulary_manager import vocabulary_manager
from prompt_manager import prompt_manager
from content_manager import content_manager
from services import (
    extract_vocabulary_from_content,
    get_all_topic_vocabulary,
    log_vocabulary_debug_info,
    manage_content_ids,
    manage_session_lifecycle,
    select_advanced_general_words,
    select_best_vocabulary_word,
)
from telemetry import (
    collect_educational_data,
    determine_story_exchange_type,
    get_latest_llm_timing,
    latency_logger,
    setup_latency_logging,
    story_tracker,
)
from models.schemas import (
    ChatRequest,
    ChatResponse,
    DesignPrompt,
    EnhancedStoryResponse,
    SessionData,
    StoryEntities,
    StoryMetadata,
    StructuredStoryResponse,
    VocabQuestion,
    VocabularyPhase,
)

# PROMPT MANAGER ARCHITECTURE:
# Centralized prompt generation with self-documenting methods for maintainability:
# 
# PROMPT MANAGER (prompt_manager.py):
# - Single source of truth for all prompt generation
# - Clear method names show complete user experience flow
# - Self-documenting: get_story_opening_prompt(), get_grammar_feedback_prompt(), etc.
# - No documentation drift: code IS the documentation
#
# BUSINESS LOGIC (this file):
# - Session state management and educational flow control
# - Clean PromptManager usage with readable method calls
# - Vocabulary tracking, anti-repetition, progress tracking
# - Educational standards validation and fallback handling
#
# BENEFITS:
# - Complete user experience visible through method names
# - Easy to find, modify, and test individual prompts
# - Inherently maintainable without separate documentation

# === LATENCY MEASUREMENT SYSTEM ===

# Latency helpers now live in telemetry.latency

# === SESSION LIFECYCLE MANAGEMENT ===

# Session lifecycle helpers now live in services.session_service

def capture_story_assessment(assessment_response: str) -> dict:
    """
    Capture LLM story assessment with comprehensive error handling
    
    Args:
        assessment_response: Raw LLM assessment response string
        
    Returns:
        Structured assessment dict with status, data, and error fields
    """
    if not assessment_response or assessment_response.strip() == "":
        return {
            "assessment_status": "empty",
            "assessment_data": None,
            "assessment_error": None
        }
    
    try:
        assessment_json = json.loads(assessment_response)
        return {
            "assessment_status": "success",
            "assessment_data": assessment_json,
            "assessment_error": None
        }
    except json.JSONDecodeError as e:
        return {
            "assessment_status": "error", 
            "assessment_data": None,
            "assessment_error": str(e)
        }

# === END LATENCY MEASUREMENT SYSTEM ===

# Design and session schemas now live in models/schemas.py

# Load centralized theme configuration
def load_theme_config():
    """Load theme configuration from centralized JSON file"""
    config_path = os.path.join(os.path.dirname(__file__), '..', 'frontend', 'config', 'theme-config.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
            logging.info("Theme configuration loaded successfully from JSON file")
            return config
    except Exception as e:
        logging.error(f"Failed to load theme configuration: {e}")
        # Fallback configuration
        return {
            "topicKeywords": {
                "space": ["space", "planet", "star", "rocket", "astronaut"],
                "animals": ["animal", "dog", "cat"],
                "fantasy": ["fantasy", "magic", "magical", "dragon", "creature"],
                "sports": ["sport", "soccer", "football"],
                "ocean": ["ocean", "sea", "fish"]
            },
            "themeMapping": {
                "fantasy": "theme-fantasy",
                "sports": "theme-sports",
                "animals": "theme-animals",
                "ocean": "theme-ocean",
                "space": "theme-space"
            },
            "defaultTheme": "theme-space"
        }

# Load theme configuration at startup
THEME_CONFIG = load_theme_config()


# REMOVED: Old prompt generation functions moved to PromptManager for better organization
# - generate_vocabulary_enhanced_prompt() -> prompt_manager.enhance_with_vocabulary()
# - generate_massive_vocabulary_pool() -> prompt_manager.generate_massive_vocabulary_pool()  
# - generate_structured_story_prompt() -> prompt_manager.get_story_opening_prompt()
#
# All prompt logic now centralized in prompt_manager.py with self-documenting method names


def parse_structured_story_response(llm_response: str) -> StructuredStoryResponse:
    """
    Parse JSON response with fallback to plain text if parsing fails
    
    Args:
        llm_response: Raw LLM response (should be JSON)
        
    Returns:
        StructuredStoryResponse with story and metadata
    """
    try:
        # Try to parse as JSON
        import json
        data = json.loads(llm_response.strip())
        
        # Validate that we have the required structure
        if not isinstance(data, dict) or "story" not in data:
            raise ValueError("Invalid JSON structure - missing 'story' field")
            
        # Create metadata with defaults if missing
        metadata_dict = data.get("metadata", {})
        
        # Validate design_options
        design_options = metadata_dict.get("design_options", [])
        if not design_options:
            logger.warning(f"⚠️ VALIDATION: design_options is empty or missing in LLM response")
            # Try to infer design options from the story content
            if metadata_dict.get("character_name") or metadata_dict.get("character_description"):
                design_options = ["character"]
                logger.info(f"🔧 AUTO-FIX: Inferred design_options as ['character'] from metadata")
        
        metadata = StoryMetadata(
            character_name=metadata_dict.get("character_name"),
            character_description=metadata_dict.get("character_description"),
            location_name=metadata_dict.get("location_name"),
            location_description=metadata_dict.get("location_description"),
            design_options=design_options,
            needs_naming=metadata_dict.get("needs_naming", False),
            entity_descriptor=metadata_dict.get("entity_descriptor")
        )
        
        return StructuredStoryResponse(
            story=data["story"],
            metadata=metadata
        )
        
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        # Fallback: treat as plain story text with empty metadata
        logging.warning(f"Failed to parse structured story response: {e}")
        logging.warning(f"Raw response: {llm_response[:200]}...")
        
        return StructuredStoryResponse(
            story=llm_response,
            metadata=StoryMetadata(design_options=[])  # No design options available
        )

def parse_enhanced_story_response(llm_response: str) -> EnhancedStoryResponse:
    """
    Parse JSON response with new entity-based metadata structure
    
    Args:
        llm_response: Raw LLM response with new entities format
        
    Returns:
        EnhancedStoryResponse with story and explicit entity lists
    """
    try:
        import json
        data = json.loads(llm_response.strip())
        
        # Validate basic structure
        if not isinstance(data, dict) or "story" not in data:
            raise ValueError("Invalid JSON structure - missing 'story' field")
            
        # Extract entities with validation
        entities_dict = data.get("entities", {})
        if not isinstance(entities_dict, dict):
            logging.warning("⚠️ VALIDATION: entities field is not a dictionary, using empty entities")
            entities_dict = {}
            
        # Validate and extract character entities
        characters_dict = entities_dict.get("characters", {})
        if not isinstance(characters_dict, dict):
            logging.warning("⚠️ VALIDATION: entities.characters is not a dictionary, using empty lists")
            characters_dict = {}
            
        named_chars = characters_dict.get("named", [])
        unnamed_chars = characters_dict.get("unnamed", [])
        
        # Validate list types
        if not isinstance(named_chars, list):
            logging.warning("⚠️ VALIDATION: characters.named is not a list, converting to empty list")
            named_chars = []
        if not isinstance(unnamed_chars, list):
            logging.warning("⚠️ VALIDATION: characters.unnamed is not a list, converting to empty list")
            unnamed_chars = []
            
        # Validate and extract location entities
        locations_dict = entities_dict.get("locations", {})
        if not isinstance(locations_dict, dict):
            logging.warning("⚠️ VALIDATION: entities.locations is not a dictionary, using empty lists")
            locations_dict = {}
            
        named_locs = locations_dict.get("named", [])
        unnamed_locs = locations_dict.get("unnamed", [])
        
        # Validate list types
        if not isinstance(named_locs, list):
            logging.warning("⚠️ VALIDATION: locations.named is not a list, converting to empty list")
            named_locs = []
        if not isinstance(unnamed_locs, list):
            logging.warning("⚠️ VALIDATION: locations.unnamed is not a list, converting to empty list")
            unnamed_locs = []
            
        # Extract vocabulary words
        vocab_words = data.get("vocabulary_words", [])
        if not isinstance(vocab_words, list):
            logging.warning("⚠️ VALIDATION: vocabulary_words is not a list, converting to empty list")
            vocab_words = []
            
        # Create validated entity structure
        entities = StoryEntities(
            characters=EntityLists(named=named_chars, unnamed=unnamed_chars),
            locations=EntityLists(named=named_locs, unnamed=unnamed_locs)
        )
        
        # Log successful parsing
        total_entities = len(named_chars) + len(unnamed_chars) + len(named_locs) + len(unnamed_locs)
        logging.info(f"✅ ENTITY PARSE: Found {total_entities} entities - "
                    f"chars({len(named_chars)} named, {len(unnamed_chars)} unnamed), "
                    f"locs({len(named_locs)} named, {len(unnamed_locs)} unnamed)")
        
        return EnhancedStoryResponse(
            story=data["story"],
            entities=entities,
            vocabulary_words=vocab_words
        )
        
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        # Fallback: treat as plain story text with empty entities
        logging.warning(f"Failed to parse enhanced story response: {e}")
        logging.warning(f"Raw response: {llm_response[:200]}...")
        
        # Try to fall back to legacy parsing
        try:
            legacy_response = parse_structured_story_response(llm_response)
            logging.info("🔧 FALLBACK: Successfully used legacy parser")
            
            # Convert legacy format to new format
            entities = StoryEntities()
            if legacy_response.metadata.character_name:
                entities.characters.named = [legacy_response.metadata.character_name]
            elif legacy_response.metadata.character_description:
                entities.characters.unnamed = [legacy_response.metadata.character_description]
                
            if legacy_response.metadata.location_name:
                entities.locations.named = [legacy_response.metadata.location_name]
            elif legacy_response.metadata.location_description:
                entities.locations.unnamed = [legacy_response.metadata.location_description]
                
            return EnhancedStoryResponse(
                story=legacy_response.story,
                entities=entities,
                vocabulary_words=[]  # Legacy format doesn't include vocab words explicitly
            )
            
        except Exception as legacy_error:
            logging.error(f"❌ FALLBACK FAILED: Legacy parser also failed: {legacy_error}")
            return EnhancedStoryResponse(
                story=llm_response,
                entities=StoryEntities(),
                vocabulary_words=[]
            )

def validate_entity_structure(entities: StoryEntities) -> bool:
    """
    Validate that entity structure has at least one designable CHARACTER entity
    (Location design temporarily disabled per user request)
    
    Args:
        entities: StoryEntities to validate
        
    Returns:
        True if there are CHARACTER entities that need design, False otherwise
    """
    # ONLY CHECK CHARACTERS - location design disabled per user request
    total_character_entities = len(entities.characters.named) + len(entities.characters.unnamed)
    
    # DISABLED: Location counting temporarily disabled per user request
    # TODO: Uncomment when location design should be re-enabled
    # total_location_entities = len(entities.locations.named) + len(entities.locations.unnamed)
    # total_entities = total_character_entities + total_location_entities
    
    if total_character_entities == 0:
        logging.warning("⚠️ VALIDATION: No CHARACTER entities found for design phase (location design disabled)")
        return False
        
    # Check for designable CHARACTER entities only
    named_characters = len(entities.characters.named) 
    unnamed_characters = len(entities.characters.unnamed)
    # DISABLED: Location design temporarily disabled per user request
    # TODO: Uncomment when location design should be re-enabled  
    # named_locations = len(entities.locations.named)
    # unnamed_locations = len(entities.locations.unnamed)
    
    total_designable = named_characters + unnamed_characters
    
    if total_designable == 0:
        logging.info("ℹ️ VALIDATION: No CHARACTER entities found - no design phase needed (location design disabled)")
        return False
        
    if unnamed_characters > 0:
        logging.info(f"✅ VALIDATION: Found {unnamed_characters} unnamed CHARACTERS for full design phase (naming + aspects)")
    if named_characters > 0:
        logging.info(f"✅ VALIDATION: Found {named_characters} named CHARACTERS for aspect design phase (skip naming)")
        
    logging.info(f"✅ VALIDATION: Total {total_designable} CHARACTER entities available for design phase (location design disabled)")
    return True

def get_next_design_entity(entities: StoryEntities, designed_entities: List[str] = None) -> Optional[Tuple[str, str, bool]]:
    """
    Get the next entity that needs design from entity lists
    
    Args:
        entities: StoryEntities with all entities
        designed_entities: List of entities already designed
        
    Returns:
        Tuple of (entity_type, entity_descriptor, is_named) or None if no more entities
        is_named indicates whether to skip naming phase (True = skip naming, False = start with naming)
    """
    if designed_entities is None:
        designed_entities = []
        
    # Priority 1: Named entities (aspect design only, skip naming)
    # When we have named characters, prioritize them first to skip naming phase
    for char in entities.characters.named:
        if char not in designed_entities:
            logging.info(f"🎯 DESIGN: Next entity is named character '{char}' (aspect design only)")
            return ("character", char, True)  # is_named = True, so skip naming
            
    # Priority 2: Unnamed entities (need full design: naming + aspects)
    # Only process unnamed entities if no named entities are available
    for char in entities.characters.unnamed:
        if char not in designed_entities:
            logging.info(f"🎯 DESIGN: Next entity is unnamed character '{char}' (full design)")
            return ("character", char, False)  # is_named = False, so start with naming
            
    # DISABLED: Location design temporarily disabled per user request
    # TODO: Uncomment when location design should be re-enabled
    # # Check unnamed locations next
    # for loc in entities.locations.unnamed:
    #     if loc not in designed_entities:
    #         logging.info(f"🎯 DESIGN: Next entity is unnamed location '{loc}' (full design)")
    #         return ("location", loc, False)  # is_named = False, so start with naming
            
    # DISABLED: Location design temporarily disabled per user request
    # TODO: Uncomment when location design should be re-enabled
    # # Check named locations
    # for loc in entities.locations.named:
    #     if loc not in designed_entities:
    #         logging.info(f"🎯 DESIGN: Next entity is named location '{loc}' (aspect design only)")
    #         return ("location", loc, True)  # is_named = True, so skip naming
            
    logging.info("✅ DESIGN: All entities have been designed")
    return None

def load_design_aspects(design_type: str) -> dict:
    """
    Load design aspects from ContentManager
    
    Args:
        design_type: "character" or "location"
        
    Returns:
        Dictionary of design aspects with prompts and vocabulary
    """
    try:
        # Get design templates from ContentManager
        design_templates = content_manager.content.get("design_templates", {})
        aspects = design_templates.get(design_type, {})
        
        if not aspects:
            logging.warning(f"No design aspects found for type: {design_type}")
            return {}
            
        logging.info(f"✅ Loaded design aspects for {design_type} from ContentManager")
        return aspects
        
    except Exception as e:
        logging.error(f"❌ Failed to load design aspects for {design_type}: {e}")
        return {}

def determine_entity_type_from_descriptor(metadata: StoryMetadata) -> Optional[str]:
    """
    Determine whether entity_descriptor refers to character or location
    by matching it with character_description or location_description
    
    Args:
        metadata: Story metadata with entity_descriptor and descriptions
        
    Returns:
        "character", "location", or None if no clear match
    """
    if not metadata.entity_descriptor:
        return None
    
    descriptor = metadata.entity_descriptor.strip().lower()
    
    # Check if entity_descriptor matches character_description
    if metadata.character_description:
        char_desc = metadata.character_description.strip().lower()
        if descriptor == char_desc or descriptor in char_desc or char_desc in descriptor:
            logger.info(f"🔍 ENTITY TYPE: '{metadata.entity_descriptor}' matches character_description '{metadata.character_description}'")
            return "character"
    
    # Check if entity_descriptor matches location_description  
    if metadata.location_description:
        loc_desc = metadata.location_description.strip().lower()
        if descriptor == loc_desc or descriptor in loc_desc or loc_desc in descriptor:
            logger.info(f"🔍 ENTITY TYPE: '{metadata.entity_descriptor}' matches location_description '{metadata.location_description}'")
            return "location"
    
    # If no clear match, log for debugging and return None (will fall back to random)
    logger.warning(f"🔍 ENTITY TYPE: Could not determine type for '{metadata.entity_descriptor}' - char: '{metadata.character_description}', loc: '{metadata.location_description}'")
    return None

def select_design_focus(character_name: Optional[str], location_name: Optional[str], design_options: List[str] = None, metadata: Optional[StoryMetadata] = None) -> Optional[str]:
    """
    Select character design only (location design temporarily disabled)
    For unnamed entities that need naming, determines type based on entity_descriptor match
    
    Args:
        character_name: Name of character if introduced
        location_name: Name of location if introduced (ignored - location design disabled)
        design_options: Available design options from metadata (only character considered)
        metadata: Full story metadata for intelligent entity type determination
        
    Returns:
        "character" or None if no character available
    """
    import random
    
    available_options = []
    
    # ONLY CHECK CHARACTERS - location design disabled per user request
    # Check named entities first
    if character_name:
        available_options.append("character")
    # DISABLED: Location design temporarily disabled per user request  
    # TODO: Uncomment when location design should be re-enabled
    # if location_name:
    #     available_options.append("location")
    
    # If no named entities but design_options available, use those (for unnamed entities)
    if not available_options and design_options:
        # ONLY consider character options - filter out location
        available_options = [option for option in design_options if option == "character"]
        logger.info(f"🔧 UNNAMED ENTITY: Using design_options {design_options} -> available (character only): {available_options}")
        
        # BUG FIX: For unnamed entities that need naming, determine entity type intelligently
        # But only return character since location is disabled
        if metadata and metadata.needs_naming and metadata.entity_descriptor:
            entity_type = determine_entity_type_from_descriptor(metadata)
            if entity_type == "character" and entity_type in available_options:
                logger.info(f"🎯 NAMING BUG FIX: entity_descriptor '{metadata.entity_descriptor}' -> entity_type '{entity_type}'")
                return entity_type
            elif entity_type == "location":
                logger.info(f"🚫 LOCATION DESIGN DISABLED: Skipping location entity '{metadata.entity_descriptor}'")
                return None  # Skip location design
    
    if not available_options:
        return None
    else:
        # Since we only consider characters now, just return the first (and only) option
        choice = available_options[0]  # Will always be "character" 
        logger.info(f"✅ CHARACTER DESIGN SELECTED: '{choice}' from {available_options}")
        return choice

def get_next_design_aspect(design_type: str, used_aspects: List[str]) -> str:
    """
    Get next design aspect using rotation logic
    
    Args:
        design_type: "character" or "location"
        used_aspects: List of aspects already used
        
    Returns:
        Next aspect to design
    """
    aspects = load_design_aspects(design_type)
    if not aspects:
        return "appearance"  # Fallback
    
    available_aspects = [aspect for aspect in aspects.keys() if aspect not in used_aspects]
    
    if not available_aspects:
        # All aspects used, start over
        available_aspects = list(aspects.keys())
    
    # Return random available aspect for variety and engagement
    import random
    selected_aspect = random.choice(available_aspects)
    logger.info(f"🎲 ASPECT SELECTION: Randomly selected '{selected_aspect}' from {available_aspects}")
    return selected_aspect

def create_design_prompt(session_data: SessionData) -> ChatResponse:
    """
    Generate design prompt based on current design phase and aspect
    
    Args:
        session_data: Current session state
        
    Returns:
        ChatResponse with designPrompt field populated
    """
    if not session_data.designPhase or not session_data.storyMetadata:
        logging.error("create_design_prompt called without active design phase")
        return ChatResponse(
            response=content_manager.get_bot_response("story_mode.continue_story"),
            sessionData=session_data
        )
    
    # Determine the subject name and description
    if session_data.designPhase == "character":
        subject_name = session_data.storyMetadata.character_name or "our character"
        subject_description = session_data.storyMetadata.character_description or ""
        # For naming phase, use the entity descriptor
        if session_data.currentDesignAspect == "naming" and session_data.storyMetadata.entity_descriptor:
            subject_descriptor = session_data.storyMetadata.entity_descriptor
        else:
            subject_descriptor = subject_description
    else:  # location
        subject_name = session_data.storyMetadata.location_name or "this place"
        subject_description = session_data.storyMetadata.location_description or ""
        # For naming phase, use the entity descriptor
        if session_data.currentDesignAspect == "naming" and session_data.storyMetadata.entity_descriptor:
            subject_descriptor = session_data.storyMetadata.entity_descriptor
        else:
            subject_descriptor = subject_description
    
    # Get the next aspect to design
    if not session_data.currentDesignAspect:
        session_data.currentDesignAspect = get_next_design_aspect(
            session_data.designPhase, 
            session_data.designAspectHistory
        )
    
    # Load design aspects for this type
    aspects = load_design_aspects(session_data.designPhase)
    aspect_data = aspects.get(session_data.currentDesignAspect, {})
    
    if not aspect_data:
        # Fallback data based on current aspect
        if session_data.currentDesignAspect == "naming":
            aspect_data = {
                "prompt_template": "What should we call {descriptor}?",
                "placeholder": "What would you like to name them?",
                "suggestions": ["Alex", "Maya", "Sam", "Riley", "Jordan", "Casey", "Taylor", "Morgan"]
            }
        else:
            aspect_data = {
                "prompt_template": f"Help us describe {subject_name}!",
                "placeholder": "Write 1-2 sentences describing them",
                "suggestions": ["wonderful", "amazing", "special", "unique", "interesting", "fantastic", "incredible", "magical"]
            }
    
    # Generate the prompt text (use descriptor for naming, name for other aspects)
    if session_data.currentDesignAspect == "naming":
        prompt_text = aspect_data.get("prompt_template", "").format(descriptor=subject_descriptor)
        placeholder_text = aspect_data.get("placeholder", "").format(descriptor=subject_descriptor)
    else:
        prompt_text = aspect_data.get("prompt_template", "").format(name=subject_name)
        placeholder_text = aspect_data.get("placeholder", "Write 1-2 sentences")
    
    # Create the design prompt
    design_prompt = DesignPrompt(
        type=session_data.designPhase,
        subject_name=subject_name,
        aspect=session_data.currentDesignAspect,
        prompt_text=prompt_text,
        suggested_words=aspect_data.get("suggestions", []),
        input_placeholder=placeholder_text
    )
    
    return ChatResponse(
        response="", # No text response when sending design prompt
        sessionData=session_data,
        designPrompt=design_prompt
    )

def should_trigger_design_phase(structured_response: StructuredStoryResponse) -> bool:
    """
    Determine if design phase should be triggered based on story response
    
    Args:
        structured_response: Parsed story response with metadata
        
    Returns:
        True if design phase should be triggered
    """
    metadata = structured_response.metadata
    
    # Only trigger if we have at least one design option
    return bool(metadata.design_options and len(metadata.design_options) > 0)

def trigger_design_phase(session_data: SessionData, structured_response: StructuredStoryResponse) -> ChatResponse:
    """
    Initialize design phase based on story metadata
    Handles naming phase first for unnamed entities, then regular design
    
    Args:
        session_data: Current session state
        structured_response: Parsed story with character/location info
        
    Returns:
        ChatResponse with design prompt (naming first if needed)
    """
    metadata = structured_response.metadata
    
    # Store the story metadata
    session_data.storyMetadata = metadata
    
    # Select what to design (character or location)
    session_data.designPhase = select_design_focus(
        metadata.character_name, 
        metadata.location_name,
        metadata.design_options,
        metadata  # Pass full metadata for intelligent entity type determination
    )
    
    if not session_data.designPhase:
        # No design options available, continue with regular story
        logging.info("No design options available, skipping design phase")
        return ChatResponse(
            response=structured_response.story,
            sessionData=session_data
        )
    
    # Reset design state
    session_data.currentDesignAspect = None
    session_data.designAspectHistory = []
    session_data.designComplete = False
    session_data.namingComplete = False  # Reset naming completion
    
    # Check if entity needs naming first
    # Only ask for naming if: needs_naming is True AND no existing name AND naming not completed
    character_has_name = metadata.character_name and metadata.character_name.strip()
    location_has_name = metadata.location_name and metadata.location_name.strip()
    entity_already_named = character_has_name or location_has_name
    
    if metadata.needs_naming and not session_data.namingComplete and not entity_already_named:
        logging.info(f"🏷️ Entity needs naming first: {metadata.entity_descriptor}")
        session_data.currentDesignAspect = "naming"
        
        # Generate naming prompt
        design_response = create_design_prompt(session_data)
        design_response.response = structured_response.story
        return design_response
    elif entity_already_named:
        logging.info(f"🏷️ Entity already named ({metadata.character_name or metadata.location_name}), skipping naming phase")
        session_data.designAspectHistory.append("naming")  # Mark naming as used so it won't be selected
    
    logging.info(f"Triggering design phase for {session_data.designPhase}: {metadata.character_name or metadata.location_name}")
    
    # Generate the design prompt (regular aspects)
    design_response = create_design_prompt(session_data)
    
    # Include the story text in the response
    design_response.response = structured_response.story
    
    return design_response

def trigger_enhanced_design_phase(session_data: SessionData, enhanced_response: EnhancedStoryResponse) -> ChatResponse:
    """
    Initialize design phase using new entity-based metadata system
    
    Args:
        session_data: Current session state
        enhanced_response: Parsed story with explicit entity lists
        
    Returns:
        ChatResponse with design prompt for first designable entity
    """
    entities = enhanced_response.entities
    
    # Initialize entity tracking if not exists
    if not hasattr(session_data, 'designedEntities'):
        session_data.designedEntities = []
    if not hasattr(session_data, 'currentEntityType'):
        session_data.currentEntityType = None
    if not hasattr(session_data, 'currentEntityDescriptor'):
        session_data.currentEntityDescriptor = None
        
    # Reset design state
    session_data.currentDesignAspect = None
    session_data.designAspectHistory = []
    session_data.designComplete = False
    session_data.namingComplete = False
    
    # Get the next entity that needs design
    next_entity = get_next_design_entity(entities, session_data.designedEntities)
    if not next_entity:
        # No more entities to design, continue with regular story
        logging.info("✅ ENHANCED DESIGN: All entities designed, continuing with story")
        return ChatResponse(
            response=enhanced_response.story,
            sessionData=session_data
        )
        
    entity_type, entity_descriptor, is_named = next_entity
    session_data.currentEntityType = entity_type
    session_data.currentEntityDescriptor = entity_descriptor
    
    # Set design phase type
    session_data.designPhase = entity_type
    
    # Determine starting aspect based on whether entity is already named
    if is_named:
        # Named entity: Skip naming, start with appearance/personality/etc
        session_data.namingComplete = True  # Mark as already named
        # Choose a random aspect for variety (appearance, personality, dreams, skills)
        import random
        aspects = ["appearance", "personality", "dreams", "skills"] 
        session_data.currentDesignAspect = random.choice(aspects)
        logging.info(f"🎯 ENHANCED DESIGN: Named entity '{entity_descriptor}' - starting with {session_data.currentDesignAspect} aspect")
    else:
        # Unnamed entity: Start with naming as before
        session_data.currentDesignAspect = "naming"
        logging.info(f"🎯 ENHANCED DESIGN: Unnamed entity '{entity_descriptor}' - starting with naming")
    
    logging.info(f"🎯 ENHANCED DESIGN: Starting design for {entity_type} '{entity_descriptor}'")
    
    # Create design prompt using the enhanced entity system
    design_response = create_enhanced_design_prompt(session_data)
    design_response.response = enhanced_response.story
    
    return design_response

def create_enhanced_design_prompt(session_data: SessionData) -> ChatResponse:
    """
    Create design prompt using enhanced entity system
    
    Args:
        session_data: Current session state with entity information
        
    Returns:
        ChatResponse with design prompt
    """
    from content_manager import content_manager
    
    entity_type = session_data.currentEntityType
    entity_descriptor = session_data.currentEntityDescriptor
    current_aspect = session_data.currentDesignAspect
    
    if current_aspect == "naming":
        # Generate naming prompt
        try:
            # Get naming templates
            naming_template = content_manager.get_design_template(f"{entity_type}.naming")
            
            # Create naming prompt
            prompt_text = f"Can you name {entity_descriptor}?"
            
            # Get name suggestions based on entity type
            if entity_type == "character":
                suggested_words = ["Alex", "Maya", "Sam", "River", "Sky", "Sage", "Blake", "Quinn"]
            else:  # location
                suggested_words = ["Crystal Palace", "Mystic Falls", "Adventure Park", "Sunset Beach", "Secret Garden", "Magic Library", "Wonder Cave", "Star Station"]
                
            placeholder = f"Enter a name for {entity_descriptor}"
            
            design_prompt = DesignPrompt(
                type=entity_type,
                subject_name=entity_descriptor,
                aspect=current_aspect,
                prompt_text=prompt_text,
                suggested_words=suggested_words,
                input_placeholder=placeholder
            )
            
            return ChatResponse(
                response="",  # No story text for design prompts
                sessionData=session_data,
                designPrompt=design_prompt
            )
            
        except Exception as e:
            logging.error(f"❌ Failed to create enhanced naming prompt: {e}")
            # Fallback to simple prompt
            design_prompt = DesignPrompt(
                type=entity_type,
                subject_name=entity_descriptor,
                aspect="naming",
                prompt_text=f"Can you name {entity_descriptor}?",
                suggested_words=["Alex", "Maya", "Sam", "River"],
                input_placeholder=f"Enter a name for {entity_descriptor}"
            )
            
            return ChatResponse(
                response="",
                sessionData=session_data,
                designPrompt=design_prompt
            )
    else:
        # Handle description aspects (appearance, personality, dreams, skills, flaws)
        try:
            # Get the named entity - prioritize enhanced system for named entities
            entity_name = "the entity"  # fallback
            
            # For enhanced system: check if we have a current entity descriptor 
            if hasattr(session_data, 'currentEntityDescriptor') and session_data.currentEntityDescriptor:
                # If this is a design phase for a named entity (already has a name in storyMetadata)
                # or if the descriptor looks like a name (single word, capitalized), use it directly
                descriptor = session_data.currentEntityDescriptor
                
                # Check if this looks like a proper name (single capitalized word)
                if descriptor and len(descriptor.split()) == 1 and descriptor[0].isupper():
                    entity_name = descriptor
                    logging.info(f"🎯 ENHANCED ENTITY NAME: Using currentEntityDescriptor '{entity_name}' (detected as proper name)")
                # Otherwise, check storyMetadata for the actual name (for entities that went through naming)
                elif session_data.storyMetadata:
                    if entity_type == "character" and session_data.storyMetadata.character_name:
                        entity_name = session_data.storyMetadata.character_name
                        logging.info(f"🎯 ENHANCED ENTITY NAME: Using storyMetadata character_name '{entity_name}' for descriptor '{descriptor}'")
                    elif entity_type == "location" and session_data.storyMetadata.location_name:
                        entity_name = session_data.storyMetadata.location_name
                        logging.info(f"🎯 ENHANCED ENTITY NAME: Using storyMetadata location_name '{entity_name}' for descriptor '{descriptor}'")
                    else:
                        # If no name in storyMetadata, use descriptor as fallback
                        entity_name = descriptor
                        logging.info(f"🎯 ENHANCED ENTITY NAME: Using currentEntityDescriptor '{entity_name}' as fallback")
                else:
                    # Use descriptor directly if no storyMetadata
                    entity_name = descriptor
                    logging.info(f"🎯 ENHANCED ENTITY NAME: Using currentEntityDescriptor '{entity_name}' (no storyMetadata)")
            
            # Legacy system fallback: check storyMetadata only
            elif session_data.storyMetadata:
                if entity_type == "character" and session_data.storyMetadata.character_name:
                    entity_name = session_data.storyMetadata.character_name
                elif entity_type == "location" and session_data.storyMetadata.location_name:
                    entity_name = session_data.storyMetadata.location_name
                logging.info(f"🎯 LEGACY ENTITY NAME: Using storyMetadata name '{entity_name}'")
            
            # Get design template for this aspect
            design_templates = content_manager.content.get("design_templates", {})
            entity_templates = design_templates.get(entity_type, {})
            aspect_template = entity_templates.get(current_aspect, {})
            
            # Extract template information
            prompt_template = aspect_template.get("prompt_template", f"Tell me about {entity_name}'s {current_aspect}")
            placeholder = aspect_template.get("placeholder", f"Describe the {current_aspect}")
            # Try both 'suggestion_words' (new format) and 'suggestions' (legacy format) 
            suggestions = aspect_template.get("suggestion_words", aspect_template.get("suggestions", []))
            
            # Format the prompt with the entity name
            prompt_text = prompt_template.format(name=entity_name, descriptor=entity_descriptor)
            
            logging.info(f"🎯 ENHANCED DESIGN: Created {current_aspect} prompt for {entity_name}")
            
            design_prompt = DesignPrompt(
                type=entity_type,
                subject_name=entity_name,
                aspect=current_aspect,
                prompt_text=prompt_text,
                suggested_words=suggestions[:8] if suggestions else [],  # Limit to 8 suggestions
                input_placeholder=placeholder
            )
            
            return ChatResponse(
                response="",
                sessionData=session_data,
                designPrompt=design_prompt
            )
            
        except Exception as e:
            logging.error(f"❌ Failed to create enhanced description prompt: {e}")
            # Final fallback to legacy system
            return create_design_prompt(session_data)

async def handle_design_phase_interaction(user_message: str, session_data: SessionData) -> ChatResponse:
    """
    Handle user input during design phase
    
    Args:
        user_message: User's design description
        session_data: Current session state
        
    Returns:
        ChatResponse with next design prompt or story continuation
    """
    # Import content_manager at function scope to avoid variable scope issues
    from content_manager import content_manager
    
    if not session_data.designPhase or not session_data.currentDesignAspect:
        logging.error("handle_design_phase_interaction called without active design phase")
        return ChatResponse(
            response=content_manager.get_bot_response("story_mode.continue_story"),
            sessionData=session_data
        )
    
    # Handle naming aspect specially
    if session_data.currentDesignAspect == "naming":
        # User provided a name - update the metadata
        provided_name = user_message.strip()
        
        # Handle both enhanced and legacy session structures
        if hasattr(session_data, 'currentEntityType') and session_data.currentEntityType:
            # Enhanced system: store the provided name and track designed entities
            if not hasattr(session_data, 'designedEntities'):
                session_data.designedEntities = []
            session_data.designedEntities.append(session_data.currentEntityDescriptor)
            
            # Store the provided name in storyMetadata for later use
            if not session_data.storyMetadata:
                session_data.storyMetadata = StoryMetadata()
            
            if session_data.currentEntityType == "character":
                session_data.storyMetadata.character_name = provided_name
            else:  # location
                session_data.storyMetadata.location_name = provided_name
            
            logging.info(f"🏷️ ENHANCED NAMING: Named '{session_data.currentEntityDescriptor}' as '{provided_name}'")
        elif session_data.storyMetadata:
            # Legacy system: update storyMetadata
            if session_data.designPhase == "character":
                session_data.storyMetadata.character_name = provided_name
            else:  # location
                session_data.storyMetadata.location_name = provided_name
            logging.info(f"🏷️ LEGACY NAMING: Updated {session_data.designPhase} name to '{provided_name}'")
        else:
            # Initialize storyMetadata if it doesn't exist
            session_data.storyMetadata = StoryMetadata()
            if session_data.designPhase == "character":
                session_data.storyMetadata.character_name = provided_name
            else:
                session_data.storyMetadata.location_name = provided_name
            logging.info(f"🏷️ INIT NAMING: Created storyMetadata and set {session_data.designPhase} name to '{provided_name}'")
        
        # Mark naming as complete
        session_data.namingComplete = True
        session_data.designAspectHistory.append("naming")
        
        # Provide positive feedback about the name choice
        feedback_response = content_manager.get_bot_response(
            "design_phase.naming_feedback", 
            provided_name=provided_name, 
            design_phase=session_data.designPhase
        )
        
        # Check if this is enhanced system and if there are more entities to design
        if hasattr(session_data, 'currentEntityType') and session_data.currentEntityType:
            # Enhanced system: continue to description phase after naming
            entity_type = session_data.currentEntityType
            
            # Get available design aspects from templates
            try:
                design_templates = content_manager.content.get("design_templates", {})
                entity_templates = design_templates.get(entity_type, {})
                
                # Available aspects excluding naming
                available_aspects = [aspect for aspect in entity_templates.keys() 
                                   if aspect != "naming" and aspect not in session_data.designAspectHistory]
                
                # Continue to description phase if we haven't done one yet (limit to 2 total: naming + 1 description)
                if available_aspects and len(session_data.designAspectHistory) < 2:
                    # Select a random aspect for variety
                    import random
                    selected_aspect = random.choice(available_aspects)
                    session_data.currentDesignAspect = selected_aspect
                    
                    logging.info(f"🎯 ENHANCED DESIGN: Continuing to {selected_aspect} aspect for {entity_type}")
                    
                    # Create transition message and description prompt
                    transition_message = f"Now let's bring {provided_name} to life with more details!"
                    feedback_with_transition = f"{feedback_response}\n\n{transition_message}"
                    
                    # Create design prompt for the selected aspect
                    design_response = create_enhanced_design_prompt(session_data)
                    design_response.response = feedback_with_transition
                    return design_response
                    
            except Exception as e:
                logging.error(f"❌ Failed to continue enhanced design phase: {e}")
            
            # Fallback or completion after description phase
            session_data.designComplete = True
            session_data.designPhase = None
            session_data.currentDesignAspect = None
            session_data.currentEntityType = None
            session_data.currentEntityDescriptor = None
            
            # Story continuation message
            completion_message = f"{feedback_response}\n\nGreat! Now that we've designed {provided_name}, let's continue with our story!"
            
            return ChatResponse(
                response=completion_message,
                sessionData=session_data
            )
        else:
            # Legacy system: continue to next design aspect
            aspects = load_design_aspects(session_data.designPhase)
            remaining_aspects = [aspect for aspect in aspects.keys() 
                                if aspect not in session_data.designAspectHistory and aspect != "naming"]
            
            if remaining_aspects and len(session_data.designAspectHistory) < 2:  # Limit to 2 aspects total
                session_data.currentDesignAspect = remaining_aspects[0]
                
                transition_message = content_manager.get_bot_response("design_phase.naming_transition", provided_name=provided_name)
                feedback_with_transition = f"{feedback_response}\n\n{transition_message}"
                design_response = create_design_prompt(session_data)
                design_response.response = feedback_with_transition
                return design_response
            else:
                # Design phase complete after naming
                session_data.designComplete = True
                session_data.designPhase = None
                session_data.currentDesignAspect = None
                
                design_completion = content_manager.get_bot_response("design_phase.design_completion_simple", feedback_response=feedback_response, subject_name=provided_name)
                completion_message = design_completion
                
                return ChatResponse(
                    response=completion_message,
                    sessionData=session_data
                )
    
    # Handle description aspects for enhanced system
    if hasattr(session_data, 'currentEntityType') and session_data.currentEntityType and session_data.currentDesignAspect != "naming":
        # Enhanced system description completion
        entity_type = session_data.currentEntityType
        provided_description = user_message.strip()
        
        # Get the entity name from storyMetadata
        entity_name = "the entity"  # fallback
        if session_data.storyMetadata:
            if entity_type == "character" and session_data.storyMetadata.character_name:
                entity_name = session_data.storyMetadata.character_name
            elif entity_type == "location" and session_data.storyMetadata.location_name:
                entity_name = session_data.storyMetadata.location_name
        
        logging.info(f"🎯 ENHANCED DESIGN: Completed {session_data.currentDesignAspect} description for {entity_name}")
        
        # Provide feedback on the description
        feedback_response = f"Wonderful description! I love how you described {entity_name}. That really brings them to life!"
        feedback_duration = 0.0  # No LLM call for hardcoded feedback
        
        # Complete design phase after description
        session_data.designComplete = True
        session_data.designPhase = None  
        session_data.currentDesignAspect = None
        session_data.currentEntityType = None
        session_data.currentEntityDescriptor = None
        
        # Generate actual story continuation (like legacy system does)
        story_context = " | ".join(session_data.storyParts[-3:]) if session_data.storyParts else ""
        design_summary = f"The child has helped design {entity_name} with these details from our design session."
        
        continuation_prompt = prompt_manager.get_design_continuation_prompt(
            session_data.topic, story_context, design_summary, provided_description, entity_name
        )
        
        enhanced_prompt, selected_vocab = prompt_manager.enhance_with_vocabulary(
            continuation_prompt, session_data.topic, 
            session_data.askedVocabWords + session_data.contentVocabulary
        )
        
        try:
            story_continuation = llm_provider.generate_response(enhanced_prompt)
            story_duration = get_latest_llm_timing()
            session_data.storyParts.append(story_continuation)
            
            # Track vocabulary from continuation
            story_vocab = extract_vocabulary_from_content(story_continuation, session_data.contentVocabulary)
            if story_vocab:
                session_data.contentVocabulary.extend(story_vocab)
                logging.info(f"📋 VOCABULARY TRACKING: Added {len(story_vocab)} words from enhanced design continuation. Total: {len(session_data.contentVocabulary)}")
            
            # Log grammar feedback immediately (sub-interaction 1)
            feedback_educational_data = collect_educational_data(
                session_data, 
                ChatResponse(response=feedback_response), 
                "storywriting", 
                provided_description, 
                ["grammar_feedback"],
                sub_interaction=1
            )
            latency_logger.log_educational_interaction("grammar_feedback", feedback_response, feedback_duration, feedback_educational_data)
            
            # Log story continuation immediately (sub-interaction 2)  
            story_educational_data = collect_educational_data(
                session_data,
                ChatResponse(response=story_continuation),
                "storywriting",
                provided_description,
                ["story_generation"], 
                sub_interaction=2
            )
            latency_logger.log_educational_interaction("story_generation", story_continuation, story_duration, story_educational_data)
            
            # Complete response with feedback + story continuation
            complete_response = f"{feedback_response}\n\nPerfect! You've helped bring {entity_name} to life! Here's how the story continues:\n\n{story_continuation}"
            
            logging.info(f"✅ ENHANCED STORY CONTINUATION: Generated continuation after designing {entity_name}")
            
            return ChatResponse(
                response=complete_response,
                sessionData=session_data,
                immediate_logging_performed=True
            )
            
        except Exception as e:
            logging.error(f"❌ Error generating enhanced story continuation after design: {e}")
            # Fallback to simple continuation message
            completion_message = f"{feedback_response}\n\nThanks for helping design {entity_name}! Let's continue our story. What happens next?"
            
            return ChatResponse(
                response=completion_message,
                sessionData=session_data
            )
    
    # Regular design aspect handling (legacy system)
    if session_data.storyMetadata:
        subject_name = (session_data.storyMetadata.character_name if session_data.designPhase == "character" 
                       else session_data.storyMetadata.location_name)
    else:
        # Fallback if storyMetadata is missing
        subject_name = "the entity"
        logging.warning("Regular design aspect handling called but storyMetadata is None")
    
    # Provide brief writing feedback (act as English tutor)
    feedback_prompt = prompt_manager.get_grammar_feedback_prompt(user_message, subject_name, session_data.designPhase)
    
    try:
        feedback_response = llm_provider.generate_response(feedback_prompt)
        feedback_duration = get_latest_llm_timing()
    except Exception as e:
        logging.error(f"Error generating writing feedback: {e}")
        feedback_response = content_manager.get_bot_response("encouragement.creative_writing")
        feedback_duration = 0.0
    
    # Add the current aspect to history
    session_data.designAspectHistory.append(session_data.currentDesignAspect)
    
    # Load design aspects to check if we should continue
    aspects = load_design_aspects(session_data.designPhase)
    remaining_aspects = [aspect for aspect in aspects.keys() 
                        if aspect not in session_data.designAspectHistory]
    
    # Decide whether to continue with more aspects (limit to 2-3 aspects total)
    should_continue_design = (
        len(session_data.designAspectHistory) < 2 and  # Limit to 2 aspects max for engagement
        len(remaining_aspects) > 0
    )
    
    if should_continue_design:
        # Continue with next aspect
        session_data.currentDesignAspect = remaining_aspects[0]
        
        # Generate response with feedback + next design prompt
        feedback_with_transition = f"{feedback_response}\n\nWonderful! Now let's add more details to make {subject_name} even more interesting!"
        
        design_response = create_design_prompt(session_data)
        design_response.response = feedback_with_transition
        
        return design_response
    
    else:
        # Design phase complete - continue with story
        session_data.designComplete = True
        session_data.currentDesignAspect = None
        
        # Create story continuation prompt that incorporates the user's designs
        story_context = " | ".join(session_data.storyParts[-3:]) if session_data.storyParts else ""
        design_summary = f"The child has helped design {subject_name} with these details from our design session."
        
        continuation_prompt = prompt_manager.get_design_continuation_prompt(
            session_data.topic, story_context, design_summary, user_message, subject_name
        )
        
        enhanced_prompt, selected_vocab = prompt_manager.enhance_with_vocabulary(
            continuation_prompt, session_data.topic, 
            session_data.askedVocabWords + session_data.contentVocabulary
        )
        
        try:
            story_continuation = llm_provider.generate_response(enhanced_prompt)
            story_duration = get_latest_llm_timing()
            session_data.storyParts.append(story_continuation)
            
            # Track vocabulary from continuation
            story_vocab = extract_vocabulary_from_content(story_continuation, session_data.contentVocabulary)
            if story_vocab:
                session_data.contentVocabulary.extend(story_vocab)
                logger.info(f"📋 VOCABULARY TRACKING: Added {len(story_vocab)} words from design continuation. Total: {len(session_data.contentVocabulary)}")
            
            # Log grammar feedback immediately (sub-interaction 1)
            print(f"🐛 DEBUG: About to log grammar feedback. Response length: {len(feedback_response)}")
            feedback_educational_data = collect_educational_data(
                session_data, 
                ChatResponse(response=feedback_response), 
                "storywriting", 
                user_message, 
                ["grammar_feedback"],
                sub_interaction=1
            )
            print(f"🐛 DEBUG: Calling log_educational_interaction for grammar_feedback")
            latency_logger.log_educational_interaction("grammar_feedback", feedback_response, feedback_duration, feedback_educational_data)
            print(f"🐛 DEBUG: Grammar feedback logging completed")
            
            # Log story continuation immediately (sub-interaction 2)  
            print(f"🐛 DEBUG: About to log story generation. Response length: {len(story_continuation)}")
            story_educational_data = collect_educational_data(
                session_data,
                ChatResponse(response=story_continuation),
                "storywriting",
                user_message,
                ["story_generation"], 
                sub_interaction=2
            )
            print(f"🐛 DEBUG: Calling log_educational_interaction for story_generation")
            latency_logger.log_educational_interaction("story_generation", story_continuation, story_duration, story_educational_data)
            print(f"🐛 DEBUG: Story generation logging completed")
            
            # Complete response with feedback + story continuation
            complete_response = f"{feedback_response}\n\nPerfect! You've helped bring {subject_name} to life! Here's how the story continues:\n\n{story_continuation}"
            
            return ChatResponse(
                response=complete_response,
                sessionData=session_data,
                immediate_logging_performed=True
            )
            
        except Exception as e:
            logging.error(f"Error generating story continuation after design: {e}")
            return ChatResponse(
                response=f"{feedback_response}\n\nThanks for helping design {subject_name}! Let's continue our story. What happens next?",
                sessionData=session_data
            )

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Initialize latency logging on startup
@app.on_event("startup")
async def startup_event():
    setup_latency_logging()
    print("Latency logging initialized with 5MB rotation")

# Allow frontend to call backend locally
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request/Response models already defined at top of file

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(chat_request: ChatRequest):
    request_start = time.perf_counter()
    
    try:
        user_message = chat_request.message
        mode = chat_request.mode
        session_data = chat_request.sessionData or SessionData()
        
        # Manage session lifecycle and track interactions
        current_time = datetime.now()
        manage_session_lifecycle(session_data, current_time)
        
        # Manage content IDs for story/funfact tracking
        manage_content_ids(session_data, mode)
        
        logger.info(f"Processing {mode} message: {user_message} [Session: {session_data.session_id[:8] if session_data.session_id else 'none'}..., Turn: {session_data.turn_id}]")
        
        if mode == "storywriting":
            story_mode = chat_request.storyMode or "auto"
            logger.info(f"🎯 STORY MODE DEBUG: Received story_mode parameter: '{story_mode}'")
            
            # Initialize story tracking if this is a new story
            if not session_data.topic or session_data.currentStep == 0:
                story_tracker.start_story(topic=session_data.topic or "unknown", mode=mode)
            
            result = await handle_storywriting(user_message, session_data, story_mode)
            
            # Log story exchange timing
            request_latency = (time.perf_counter() - request_start) * 1000
            exchange_type = determine_story_exchange_type(session_data, result)
            story_tracker.log_exchange(
                exchange_type=exchange_type,
                latency=request_latency,
                user_input=user_message,
                response_type=getattr(result, 'response_type', 'story_response')
            )
            
            # Check if story completed and log summary
            if result.sessionData and result.sessionData.isComplete:
                story_summary = story_tracker.complete_story(
                    topic=result.sessionData.topic or "unknown",
                    mode=mode
                )
                # Add summary to response for frontend logging
                if hasattr(result, 'latency_summary'):
                    result.latency_summary = story_summary
            
            # Collect educational logging data (skip if immediate logging was already performed)
            if not getattr(result, 'immediate_logging_performed', False):
                llm_call_types = latency_logger.get_current_llm_call_types()
                educational_data = collect_educational_data(
                    session_data=session_data,
                    response=result,
                    mode=mode,
                    user_input=user_message,
                    llm_call_types=llm_call_types
                )
                
                # Attach educational data to result for latency logger
                result.educational_data = educational_data
            
            return result
            
        elif mode == "funfacts":
            result = await handle_funfacts(user_message, session_data)
            
            # Collect educational logging data (skip if immediate logging was already performed)
            if not getattr(result, 'immediate_logging_performed', False):
                llm_call_types = latency_logger.get_current_llm_call_types()
                educational_data = collect_educational_data(
                    session_data=session_data,
                    response=result,
                    mode=mode,
                    user_input=user_message,
                    llm_call_types=llm_call_types
                )
                
                # Attach educational data to result for latency logger
                result.educational_data = educational_data
            
            return result
        else:
            return ChatResponse(response=content_manager.get_bot_response("errors.mode_error"))
            
    except Exception as e:
        logger.error(f"Error processing chat request: {e}")
        return ChatResponse(response=content_manager.get_bot_response("errors.processing_error"))

async def handle_storywriting(user_message: str, session_data: SessionData, story_mode: str = "auto") -> ChatResponse:
    """Handle storywriting mode interactions following the 10-step process"""
    
    # Handle distinct vocabulary phase messages
    if user_message == "start_vocabulary":
        return await handle_start_vocabulary(session_data)
    elif user_message == "next_vocabulary":
        return await handle_next_vocabulary(session_data)
    elif user_message == "finish_vocabulary":
        return await handle_finish_vocabulary(session_data)
    
    # Handle design phase interactions
    if session_data.designPhase and not session_data.designComplete:
        return await handle_design_phase_interaction(user_message, session_data)
    
    # If no topic is set, user is choosing a topic (Step 1)
    if not session_data.topic:
        # Extract topic from user message
        topic = extract_topic_from_message(user_message)
        session_data.topic = topic
        session_data.currentStep = 2  # Moving to Step 2 after topic selection
        
        # Generate story beginning with structured response (includes character/location metadata)
        structured_prompt = prompt_manager.get_story_opening_prompt(topic, story_mode)
        enhanced_prompt, selected_vocab = prompt_manager.enhance_with_vocabulary(
            structured_prompt, topic, session_data.askedVocabWords
        )
        
        raw_response = llm_provider.generate_response(enhanced_prompt)
        logger.info(f"🎯 LLM RESPONSE DEBUG: Raw response length: {len(raw_response)} characters")
        logger.info(f"🎯 LLM RESPONSE DEBUG: First 200 chars: {raw_response[:200]}...")
        
        # Try new entity-based parsing first, fall back to legacy if needed
        try:
            enhanced_response = parse_enhanced_story_response(raw_response)
            logger.info(f"✅ ENTITY PARSE: Successfully parsed with new entity system")
            logger.info(f"🎯 ENTITY DEBUG: Characters named={enhanced_response.entities.characters.named}, unnamed={enhanced_response.entities.characters.unnamed}")
            logger.info(f"🎯 ENTITY DEBUG: Locations named={enhanced_response.entities.locations.named}, unnamed={enhanced_response.entities.locations.unnamed}")
            logger.info(f"🎯 ENTITY DEBUG: Vocabulary words: {enhanced_response.vocabulary_words}")
            
            # Add story to parts for tracking
            session_data.storyParts.append(enhanced_response.story)
            
            # Track vocabulary words from entity metadata (more reliable than content extraction)
            if enhanced_response.vocabulary_words:
                session_data.contentVocabulary.extend(enhanced_response.vocabulary_words)
                logger.info(f"📋 VOCABULARY TRACKING: Added {len(enhanced_response.vocabulary_words)} words from entity metadata. Total tracked: {len(session_data.contentVocabulary)}")
            else:
                # Fallback: extract from content if metadata doesn't have vocab words
                story_vocab_words = extract_vocabulary_from_content(enhanced_response.story, session_data.contentVocabulary)
                if story_vocab_words:
                    session_data.contentVocabulary.extend(story_vocab_words)
                    logger.info(f"📋 VOCABULARY TRACKING: Fallback - Added {len(story_vocab_words)} words from story content. Total tracked: {len(session_data.contentVocabulary)}")
            
            # Log vocabulary debug info  
            log_vocabulary_debug_info(
                topic, session_data.askedVocabWords, enhanced_response.story, "Initial Story Generation", len(session_data.contentVocabulary)
            )
            
            # Check if design phase should be triggered using new entity validation
            should_trigger = validate_entity_structure(enhanced_response.entities)
            logger.info(f"🎯 DESIGN PHASE DEBUG: validate_entity_structure() returned: {should_trigger}")
            
            # Store enhanced response for design phase use
            structured_response = enhanced_response
            
        except Exception as e:
            logger.warning(f"⚠️ FALLBACK: Enhanced parsing failed, using legacy parser: {e}")
            structured_response = parse_structured_story_response(raw_response)
            logger.info(f"🎯 LEGACY DEBUG: Parsed metadata: {structured_response.metadata}")
            logger.info(f"🎯 LEGACY DEBUG: design_options: {structured_response.metadata.design_options}")
            
            # Add story to parts for tracking
            session_data.storyParts.append(structured_response.story)
            
            # Track vocabulary words using legacy method
            story_vocab_words = extract_vocabulary_from_content(structured_response.story, session_data.contentVocabulary)
            if story_vocab_words:
                session_data.contentVocabulary.extend(story_vocab_words)
                logger.info(f"📋 VOCABULARY TRACKING: Legacy - Added {len(story_vocab_words)} words from story content. Total tracked: {len(session_data.contentVocabulary)}")
            
            # Log vocabulary debug info  
            log_vocabulary_debug_info(
                topic, session_data.askedVocabWords, structured_response.story, "Initial Story Generation", len(session_data.contentVocabulary)
            )
            
            # Check design phase using legacy method
            should_trigger = should_trigger_design_phase(structured_response)
            logger.info(f"🎯 DESIGN PHASE DEBUG: Legacy should_trigger_design_phase() returned: {should_trigger}")
        
        if should_trigger:
            # Log appropriate information based on response type
            if hasattr(structured_response, 'entities'):
                # Enhanced response
                total_entities = (len(structured_response.entities.characters.unnamed) + 
                                len(structured_response.entities.locations.unnamed))
                logger.info(f"✅ DESIGN PHASE: Triggering design phase with {total_entities} designable entities")
                design_response = trigger_enhanced_design_phase(session_data, structured_response)
            else:
                # Legacy response
                logger.info(f"✅ DESIGN PHASE: Triggering legacy design phase with options: {structured_response.metadata.design_options}")
                design_response = trigger_design_phase(session_data, structured_response)
                
            design_response.suggestedTheme = get_theme_suggestion(topic)
            return design_response
        
        # No design phase needed, continue with regular story
        logger.info(f"❌ DESIGN PHASE: Skipping design phase - no designable entities found")
        suggested_theme = get_theme_suggestion(topic)
        
        return ChatResponse(
            response=structured_response.story,
            sessionData=session_data,
            suggestedTheme=suggested_theme
        )
    
    # Check if story is already complete - handle vocabulary questions or new story
    elif session_data.isComplete:
        message_lower = user_message.lower().strip()
        
        # If we're awaiting confirmation for a new story
        if session_data.awaiting_story_confirmation:
            # Check for confirmation signals
            positive_responses = ["yes", "yeah", "yep", "sure", "ok", "okay", "i want", "let's", "space", "fantasy", "sports", "ocean", "animals", "mystery", "adventure", "food", "creative", "magic"]
            negative_responses = ["no", "nah", "not now", "maybe later", "i'm done", "that's it", "bye"]
            
            # Check if user is declining
            if any(neg in message_lower for neg in negative_responses):
                # User doesn't want another story
                session_data.awaiting_story_confirmation = False
                return ChatResponse(
                    response=content_manager.get_bot_response("story_mode.session_goodbye"),
                    sessionData=session_data
                )
            
            # Check if user is confirming (either explicitly or by mentioning a topic)
            elif any(pos in message_lower for pos in positive_responses) or len(user_message.split()) >= 1:
                # User wants to write another story - extract topic
                potential_new_topic = extract_topic_from_message(user_message)
                
                # Reset session data for new story
                session_data.topic = potential_new_topic
                session_data.storyParts = []
                session_data.currentStep = 2
                session_data.isComplete = False
                session_data.askedVocabWords = []
                session_data.awaiting_story_confirmation = False
                session_data.vocabularyPhase = VocabularyPhase()  # Reset vocabulary phase
                session_data.contentVocabulary = []  # Reset content vocabulary for new story
                
                # Reset ALL design phase fields for new story
                session_data.designPhase = None
                session_data.currentDesignAspect = None
                session_data.designAspectHistory = []
                session_data.storyMetadata = None
                session_data.designComplete = False
                session_data.namingComplete = False
                session_data.designedEntities = []
                session_data.currentEntityType = None
                session_data.currentEntityDescriptor = None
                session_data.storyPhase = None
                session_data.conflictType = None
                session_data.conflictScale = None
                session_data.narrativeAssessment = None
                
                # Generate story beginning with enhanced entity metadata system
                story_prompt = prompt_manager.get_story_opening_prompt(potential_new_topic, "auto")
                raw_response = llm_provider.generate_response(story_prompt)
                
                # Parse response using same logic as first story (enhanced entity system)
                try:
                    # Try enhanced parsing first (with entity metadata)
                    enhanced_response = parse_enhanced_story_response(raw_response)
                    logger.info(f"✅ ENTITY PARSE: Found {len(enhanced_response.entities.characters.named + enhanced_response.entities.characters.unnamed)} characters, {len(enhanced_response.entities.locations.named + enhanced_response.entities.locations.unnamed)} locations")
                    
                    # Add story to parts and track vocabulary
                    session_data.storyParts.append(enhanced_response.story)
                    
                    # Track vocabulary using enhanced method
                    if enhanced_response.vocabulary_words:
                        session_data.contentVocabulary.extend(enhanced_response.vocabulary_words)
                        logger.info(f"📋 VOCABULARY TRACKING: Enhanced - Added {len(enhanced_response.vocabulary_words)} words from entity metadata. Total tracked: {len(session_data.contentVocabulary)}")
                    
                    # Log vocabulary debug info
                    log_vocabulary_debug_info(
                        potential_new_topic, session_data.askedVocabWords, enhanced_response.story, "New Story Generation", len(session_data.contentVocabulary)
                    )
                    
                    # Check if design phase should be triggered
                    should_trigger = validate_entity_structure(enhanced_response.entities)
                    logger.info(f"🎯 NEW STORY DESIGN: validate_entity_structure() returned: {should_trigger}")
                    
                    if should_trigger:
                        # Trigger design phase for new story
                        total_entities = (len(enhanced_response.entities.characters.unnamed) + 
                                        len(enhanced_response.entities.locations.unnamed))
                        logger.info(f"✅ NEW STORY DESIGN: Triggering design phase with {total_entities} designable entities")
                        design_response = trigger_enhanced_design_phase(session_data, enhanced_response)
                        design_response.response = f"Great choice! Let's write a {potential_new_topic} story! 🌟\n\n{enhanced_response.story}"
                        design_response.suggestedTheme = get_theme_suggestion(potential_new_topic)
                        return design_response
                    
                    # No design phase needed for new story
                    logger.info(f"❌ NEW STORY DESIGN: Skipping design phase - no designable entities found")
                    return ChatResponse(
                        response=f"Great choice! Let's write a {potential_new_topic} story! 🌟\n\n{enhanced_response.story}",
                        sessionData=session_data,
                        suggestedTheme=get_theme_suggestion(potential_new_topic)
                    )
                    
                except Exception as e:
                    logger.warning(f"⚠️ NEW STORY FALLBACK: Enhanced parsing failed, using simple generation: {e}")
                    # Fallback to simple story generation
                    base_prompt = prompt_manager.get_topic_selection_story_prompt(potential_new_topic)
                    enhanced_prompt, selected_vocab = prompt_manager.enhance_with_vocabulary(
                        base_prompt, potential_new_topic, session_data.askedVocabWords
                    )
                    story_response = llm_provider.generate_response(enhanced_prompt)
                    session_data.storyParts.append(story_response)
                    
                    # Track vocabulary words from fallback
                    if selected_vocab:
                        session_data.contentVocabulary.extend(selected_vocab)
                        logger.info(f"📋 VOCABULARY TRACKING: Fallback - Added {len(selected_vocab)} words. Total: {len(session_data.contentVocabulary)}")
                    
                    log_vocabulary_debug_info(
                        potential_new_topic, session_data.askedVocabWords, story_response, "New Story Generation (Fallback)", len(session_data.contentVocabulary)
                    )
                    
                    return ChatResponse(
                        response=f"Great choice! Let's write a {potential_new_topic} story! 🌟\n\n{story_response}",
                        sessionData=session_data,
                        suggestedTheme=get_theme_suggestion(potential_new_topic)
                    )
            else:
                # Unclear response - ask for clarification
                return ChatResponse(
                    response=content_manager.get_bot_response("errors.clarification_needed"),
                    sessionData=session_data
                )
        
        # If not awaiting confirmation, check for spontaneous new topic requests (old logic)
        else:
            # Skip topic detection for generic responses or questions about vocabulary
            generic_responses = ["yes", "no", "ok", "okay", "sure", "thanks", "thank you", "great", "cool", "awesome", "nice"]
            vocab_related = ["what", "how", "why", "when", "where", "explain", "tell me", "show me"]
            
            should_check_for_new_topic = (
                len(user_message.split()) >= 2 and  # Message has at least 2 words
                not any(response in message_lower for response in generic_responses) and
                not any(word in message_lower for word in vocab_related) and
                not message_lower.startswith(("i ", "we ", "that ", "this ", "it "))  # Avoid pronouns that refer to current story
            )
            
            if should_check_for_new_topic:
                potential_new_topic = extract_topic_from_message(user_message)
                if potential_new_topic and potential_new_topic != session_data.topic:
                    # User wants to start a new story - reset session data
                    session_data.topic = potential_new_topic
                    session_data.storyParts = []
                    session_data.currentStep = 2
                    session_data.isComplete = False
                    session_data.askedVocabWords = []
                    session_data.awaiting_story_confirmation = False
                    session_data.vocabularyPhase = VocabularyPhase()  # Reset vocabulary phase
                    session_data.contentVocabulary = []  # Reset content vocabulary for new story
                    
                    # Reset ALL design phase fields for new story (topic switch)
                    session_data.designPhase = None
                    session_data.currentDesignAspect = None
                    session_data.designAspectHistory = []
                    session_data.storyMetadata = None
                    session_data.designComplete = False
                    session_data.namingComplete = False
                    session_data.designedEntities = []
                    session_data.currentEntityType = None
                    session_data.currentEntityDescriptor = None
                    session_data.storyPhase = None
                    session_data.conflictType = None
                    session_data.conflictScale = None
                    session_data.narrativeAssessment = None
                    
                    # Generate story beginning with enhanced entity metadata system (topic switch)
                    story_prompt = prompt_manager.get_story_opening_prompt(potential_new_topic, "auto")
                    raw_response = llm_provider.generate_response(story_prompt)
                    
                    # Parse response using same logic as first story (enhanced entity system)
                    try:
                        # Try enhanced parsing first (with entity metadata)
                        enhanced_response = parse_enhanced_story_response(raw_response)
                        logger.info(f"✅ TOPIC SWITCH PARSE: Found {len(enhanced_response.entities.characters.named + enhanced_response.entities.characters.unnamed)} characters, {len(enhanced_response.entities.locations.named + enhanced_response.entities.locations.unnamed)} locations")
                        
                        # Add story to parts and track vocabulary
                        session_data.storyParts.append(enhanced_response.story)
                        
                        # Track vocabulary using enhanced method
                        if enhanced_response.vocabulary_words:
                            session_data.contentVocabulary.extend(enhanced_response.vocabulary_words)
                            logger.info(f"📋 VOCABULARY TRACKING: Topic Switch Enhanced - Added {len(enhanced_response.vocabulary_words)} words from entity metadata. Total tracked: {len(session_data.contentVocabulary)}")
                        
                        # Log vocabulary debug info
                        log_vocabulary_debug_info(
                            potential_new_topic, session_data.askedVocabWords, enhanced_response.story, "Story Topic Switch", len(session_data.contentVocabulary)
                        )
                        
                        # Check if design phase should be triggered
                        should_trigger = validate_entity_structure(enhanced_response.entities)
                        logger.info(f"🎯 TOPIC SWITCH DESIGN: validate_entity_structure() returned: {should_trigger}")
                        
                        if should_trigger:
                            # Trigger design phase for topic switch story
                            total_entities = (len(enhanced_response.entities.characters.unnamed) + 
                                            len(enhanced_response.entities.locations.unnamed))
                            logger.info(f"✅ TOPIC SWITCH DESIGN: Triggering design phase with {total_entities} designable entities")
                            design_response = trigger_enhanced_design_phase(session_data, enhanced_response)
                            design_response.response = enhanced_response.story
                            design_response.suggestedTheme = get_theme_suggestion(potential_new_topic)
                            return design_response
                        
                        # No design phase needed for topic switch story
                        logger.info(f"❌ TOPIC SWITCH DESIGN: Skipping design phase - no designable entities found")
                        return ChatResponse(
                            response=enhanced_response.story,
                            sessionData=session_data,
                            suggestedTheme=get_theme_suggestion(potential_new_topic)
                        )
                        
                    except Exception as e:
                        logger.warning(f"⚠️ TOPIC SWITCH FALLBACK: Enhanced parsing failed, using simple generation: {e}")
                        # Fallback to simple story generation
                        base_prompt = prompt_manager.get_topic_selection_story_prompt(potential_new_topic)
                        enhanced_prompt, selected_vocab = prompt_manager.enhance_with_vocabulary(
                            base_prompt, potential_new_topic, session_data.askedVocabWords
                        )
                        story_response = llm_provider.generate_response(enhanced_prompt)
                        session_data.storyParts.append(story_response)
                        
                        # Track vocabulary words from fallback
                        if selected_vocab:
                            session_data.contentVocabulary.extend(selected_vocab)
                            logger.info(f"📋 VOCABULARY TRACKING: Topic Switch Fallback - Added {len(selected_vocab)} words. Total: {len(session_data.contentVocabulary)}")
                        
                        log_vocabulary_debug_info(
                            potential_new_topic, session_data.askedVocabWords, story_response, "Story Topic Switch (Fallback)", len(session_data.contentVocabulary)
                        )
                        
                        return ChatResponse(
                            response=story_response,
                            sessionData=session_data,
                            suggestedTheme=get_theme_suggestion(potential_new_topic)
                        )
        
        # Story is done - vocabulary phase will be handled by new system
        # Mark story as complete and let the frontend trigger vocabulary phase
        return ChatResponse(
            response=content_manager.get_bot_response("story_mode.story_ending"),
            sessionData=session_data
        )
    
    # Story is in progress (Steps 5-6)
    else:
        # Add user's contribution to story
        session_data.storyParts.append(f"User: {user_message}")
        
        # Provide grammar feedback if needed (Step 5)
        grammar_feedback = llm_provider.provide_grammar_feedback(user_message)
        feedback_duration = get_latest_llm_timing()
        
        # Generate next part of story (Steps 2-4 repeated)
        story_context = "\n".join(session_data.storyParts[-3:])  # Last 3 parts for context
        
        # ENHANCED STORY STRUCTURE: Intelligent story assessment
        # Replace rigid character count + step rules with narrative intelligence
        
        # Step 1: Assess current story narrative structure
        if session_data.currentStep >= 3:  # Only assess after some story development
            try:
                # Get story arc assessment from LLM
                assessment_prompt = prompt_manager.get_story_arc_assessment_prompt(
                    session_data.storyParts, session_data.topic
                )
                assessment_response = llm_provider.generate_response(assessment_prompt)
                assessment_duration = get_latest_llm_timing()
                
                # Log story assessment individually
                assessment_educational_data = collect_educational_data(
                    session_data,
                    ChatResponse(response=assessment_response),
                    "storywriting",
                    user_message,
                    ["story_assessment"],
                    sub_interaction=1
                )
                latency_logger.log_educational_interaction("story_assessment", assessment_response, assessment_duration, assessment_educational_data)
                
                # Parse assessment JSON
                import json
                assessment = json.loads(assessment_response)
                
                # Update session data with assessment
                session_data.narrativeAssessment = assessment
                session_data.storyPhase = assessment.get('current_phase', 'development')
                session_data.characterGrowthScore = assessment.get('character_growth', 0)
                session_data.completenessScore = assessment.get('completeness_score', 0)
                session_data.conflictType = assessment.get('conflict_type', 'none')
                
                logger.info(f"📖 STORY ARC ASSESSMENT: Phase={session_data.storyPhase}, Growth={session_data.characterGrowthScore}%, Complete={session_data.completenessScore}%, Conflict={session_data.conflictType}")
                
            except Exception as e:
                logger.error(f"❌ Story assessment failed: {e}, falling back to quality gates")
                session_data.narrativeAssessment = None
        
        # Step 2: Intelligent story ending decision  
        should_end_story, ending_reason = prompt_manager.should_end_story_intelligently(session_data)
        logger.info(f"📖 STORY ENDING DECISION: {should_end_story}, reason: {ending_reason}")
        
        if should_end_story:
            # End the story with vocabulary integration
            base_prompt = prompt_manager.get_story_ending_prompt(session_data.topic, story_context)
            enhanced_prompt, selected_vocab = prompt_manager.enhance_with_vocabulary(
                base_prompt, session_data.topic, session_data.askedVocabWords + session_data.contentVocabulary
            )
            story_response = llm_provider.generate_response(enhanced_prompt)
            story_duration = get_latest_llm_timing()
            
            # Log story ending generation individually
            ending_educational_data = collect_educational_data(
                session_data,
                ChatResponse(response=story_response),
                "storywriting",
                user_message,
                ["story_ending"],
                sub_interaction=1
            )
            latency_logger.log_educational_interaction("story_ending", story_response, story_duration, ending_educational_data)
            
            # Track vocabulary words that were intended to be used
            if selected_vocab:
                logger.info(f"Story ending included vocabulary: {selected_vocab}")
                session_data.contentVocabulary.extend(selected_vocab)
                logger.info(f"📋 VOCABULARY TRACKING: Added {len(selected_vocab)} words to session. Total tracked: {len(session_data.contentVocabulary)}")
            
            # Log vocabulary debug info to server logs
            log_vocabulary_debug_info(
                session_data.topic, session_data.askedVocabWords + session_data.contentVocabulary, story_response, "Story Ending", len(session_data.contentVocabulary)
            )
            
            # Add grammar feedback if available
            if grammar_feedback:
                story_response = grammar_feedback + "\n\n" + story_response
            
            session_data.storyParts.append(story_response)
            session_data.isComplete = True
            
            # DO NOT send vocabulary questions immediately with story ending
            # They will be sent in a follow-up interaction after user sees "The end!"
            
            return ChatResponse(
                response=story_response,
                vocabQuestion=None,  # No vocab question with story ending
                sessionData=session_data
            )
        else:
            # Continue story with narrative-aware prompts based on assessment
            if session_data.narrativeAssessment:
                # Use intelligent narrative continuation based on story arc assessment
                base_prompt = prompt_manager.get_narrative_continuation_prompt(
                    session_data.narrativeAssessment, session_data.topic, story_context
                )
                logger.info(f"📖 NARRATIVE CONTINUATION: Using phase-aware prompt for {session_data.storyPhase} phase")
            else:
                # Fallback to standard continuation prompt
                base_prompt = prompt_manager.get_continue_story_prompt(session_data.topic, story_context)
                logger.info(f"📖 NARRATIVE CONTINUATION: Using standard continuation prompt")
            
            # Add conflict integration if story lacks clear conflict
            if (session_data.narrativeAssessment and 
                not session_data.narrativeAssessment.get('has_clear_conflict', False) and 
                session_data.currentStep <= 3):
                
                # Add age-appropriate conflict to enhance story
                conflict_prompt = prompt_manager.get_conflict_integration_prompt(
                    session_data.topic, session_data.conflictType, session_data.conflictScale
                )
                base_prompt += f"\n\nADDITIONAL GUIDANCE: {conflict_prompt}"
                logger.info(f"📖 CONFLICT INTEGRATION: Added conflict guidance for {session_data.topic} story")
            
            enhanced_prompt, selected_vocab = prompt_manager.enhance_with_vocabulary(
                base_prompt, session_data.topic, session_data.askedVocabWords + session_data.contentVocabulary
            )
            story_response = llm_provider.generate_response(enhanced_prompt)
            story_duration = get_latest_llm_timing()
            
            # Track vocabulary words that were intended to be used
            if selected_vocab:
                logger.info(f"Story continuation included vocabulary: {selected_vocab}")
                session_data.contentVocabulary.extend(selected_vocab)
                logger.info(f"📋 VOCABULARY TRACKING: Added {len(selected_vocab)} words to session. Total tracked: {len(session_data.contentVocabulary)}")
            
            # Log vocabulary debug info to server logs
            log_vocabulary_debug_info(
                session_data.topic, session_data.askedVocabWords + session_data.contentVocabulary, story_response, "Story Continuation", len(session_data.contentVocabulary)
            )
            
            # Log individual educational interactions
            if grammar_feedback:
                # Log grammar feedback immediately
                feedback_educational_data = collect_educational_data(
                    session_data, 
                    ChatResponse(response=grammar_feedback), 
                    "storywriting", 
                    user_message, 
                    ["grammar_feedback"],
                    sub_interaction=1
                )
                latency_logger.log_educational_interaction("grammar_feedback", grammar_feedback, feedback_duration, feedback_educational_data)
            
            # Log story continuation immediately  
            story_educational_data = collect_educational_data(
                session_data,
                ChatResponse(response=story_response),
                "storywriting",
                user_message,
                ["story_generation"], 
                sub_interaction=2
            )
            latency_logger.log_educational_interaction("story_generation", story_response, story_duration, story_educational_data)
            
            # Add grammar feedback if available
            if grammar_feedback:
                story_response = grammar_feedback + "\n\n" + story_response
            
            session_data.storyParts.append(story_response)
            session_data.currentStep += 1
            
            return ChatResponse(
                response=story_response,
                sessionData=session_data
            )

async def handle_start_vocabulary(session_data: SessionData) -> ChatResponse:
    """Start vocabulary phase after story completion"""
    logger.info("Starting vocabulary phase")
    
    # Activate vocabulary phase
    session_data.vocabularyPhase.isActive = True
    session_data.vocabularyPhase.questionsAsked = 0
    session_data.vocabularyPhase.isComplete = False
    
    # Extract vocabulary words from the generated story content
    all_story_text = "\n".join(session_data.storyParts)
    content_vocab_words = extract_vocabulary_from_content(all_story_text, session_data.contentVocabulary)
    
    # DEBUG: Log vocabulary processing for first question
    logger.info("🔍 VOCAB DEBUG - First vocabulary question processing:")
    logger.info(f"  Received askedVocabWords: {session_data.askedVocabWords}")
    logger.info(f"  Content vocabulary words found: {content_vocab_words}")
    
    # Find a vocabulary word that hasn't been asked yet
    available_words = [word for word in content_vocab_words if word not in session_data.askedVocabWords]
    logger.info(f"  Available words (not asked yet): {available_words}")
    
    if available_words:
        # Use a word from the story content, prioritizing lowercase words over proper nouns
        selected_word = select_best_vocabulary_word(available_words)
        logger.info(f"  Selected word: '{selected_word}'")
        
        session_data.askedVocabWords.append(selected_word)
        session_data.vocabularyPhase.questionsAsked = 1
        
        logger.info(f"  Updated askedVocabWords: {session_data.askedVocabWords}")
        
        # Use the actual story content as context for the vocabulary question
        vocab_question = llm_provider.generate_vocabulary_question(selected_word, context=all_story_text)
        vocab_duration = get_latest_llm_timing()
        logger.info(f"  Generated question: '{vocab_question.get('question', 'N/A')}'")
        
        # Log vocabulary question generation individually
        vocab_educational_data = collect_educational_data(
            session_data,
            ChatResponse(response=json.dumps(vocab_question)),
            "storywriting",
            "start_vocabulary",
            ["vocabulary_question"],
            sub_interaction=1
        )
        latency_logger.log_educational_interaction("vocabulary_question", json.dumps(vocab_question), vocab_duration, vocab_educational_data)
        
        return ChatResponse(
            response=content_manager.get_bot_response("vocabulary.intro_after_story"),
            vocabQuestion=VocabQuestion(**vocab_question),
            sessionData=session_data
        )
    else:
        # Fallback to curated vocabulary if no words found in content
        vocab_word_data = vocabulary_manager.select_vocabulary_word(
            topic=session_data.topic or "general",
            used_words=session_data.askedVocabWords
        )
        
        if vocab_word_data:
            session_data.askedVocabWords.append(vocab_word_data['word'])
            session_data.vocabularyPhase.questionsAsked = 1
            vocab_question = llm_provider.generate_vocabulary_question(
                vocab_word_data['word'], 
                context=vocab_word_data['definition']
            )
            vocab_duration = get_latest_llm_timing()
            
            # Log fallback vocabulary question generation individually
            vocab_educational_data = collect_educational_data(
                session_data,
                ChatResponse(response=json.dumps(vocab_question)),
                "storywriting",
                "start_vocabulary_fallback",
                ["vocabulary_question"],
                sub_interaction=1
            )
            latency_logger.log_educational_interaction("vocabulary_question", json.dumps(vocab_question), vocab_duration, vocab_educational_data)
            
            return ChatResponse(
                response=content_manager.get_bot_response("vocabulary.intro_after_story"),
                vocabQuestion=VocabQuestion(**vocab_question),
                sessionData=session_data
            )
    
    # If no vocabulary words available, finish vocabulary phase
    return await handle_finish_vocabulary(session_data)

async def handle_next_vocabulary(session_data: SessionData) -> ChatResponse:
    """Request next vocabulary question with count validation"""
    logger.info(f"Requesting vocabulary question {session_data.vocabularyPhase.questionsAsked + 1} of {session_data.vocabularyPhase.maxQuestions}")
    
    # Check if we've reached the maximum
    if session_data.vocabularyPhase.questionsAsked >= session_data.vocabularyPhase.maxQuestions:
        logger.info("Max vocabulary questions reached, finishing vocabulary phase")
        return await handle_finish_vocabulary(session_data)
    
    # Extract vocabulary words from the generated story content (same approach as handle_start_vocabulary)
    all_story_text = "\n".join(session_data.storyParts)
    content_vocab_words = extract_vocabulary_from_content(all_story_text, session_data.contentVocabulary)
    
    # DEBUG: Log vocabulary processing for next question
    logger.info("🔍 VOCAB DEBUG - Next vocabulary question processing:")
    logger.info(f"  Received askedVocabWords: {session_data.askedVocabWords}")
    logger.info(f"  Content vocabulary words found: {content_vocab_words}")
    
    # Find a vocabulary word that hasn't been asked yet
    available_words = [word for word in content_vocab_words if word not in session_data.askedVocabWords]
    logger.info(f"  Available words (not asked yet): {available_words}")
    
    if available_words:
        # Use a word from the story content, prioritizing lowercase words over proper nouns
        selected_word = select_best_vocabulary_word(available_words)
        logger.info(f"  Selected word: '{selected_word}'")
        
        session_data.askedVocabWords.append(selected_word)
        session_data.vocabularyPhase.questionsAsked += 1
        
        logger.info(f"  Updated askedVocabWords: {session_data.askedVocabWords}")
        logger.info(f"  Updated questionsAsked: {session_data.vocabularyPhase.questionsAsked}")
        
        # Use the actual story content as context for the vocabulary question
        vocab_question = llm_provider.generate_vocabulary_question(selected_word, context=all_story_text)
        vocab_duration = get_latest_llm_timing()
        logger.info(f"  Generated question: '{vocab_question.get('question', 'N/A')}'")
        
        # Log vocabulary question generation individually
        vocab_educational_data = collect_educational_data(
            session_data,
            ChatResponse(response=json.dumps(vocab_question)),
            "storywriting",
            "next_vocabulary",
            ["vocabulary_question"],
            sub_interaction=1
        )
        latency_logger.log_educational_interaction("vocabulary_question", json.dumps(vocab_question), vocab_duration, vocab_educational_data)
        
        return ChatResponse(
            response=content_manager.get_bot_response("vocabulary.next_question"),
            vocabQuestion=VocabQuestion(**vocab_question),
            sessionData=session_data
        )
    else:
        # Fallback to curated vocabulary if no more words found in content
        vocab_word_data = vocabulary_manager.select_vocabulary_word(
            topic=session_data.topic or "general",
            used_words=session_data.askedVocabWords
        )
        
        if vocab_word_data:
            session_data.askedVocabWords.append(vocab_word_data['word'])
            session_data.vocabularyPhase.questionsAsked += 1
            vocab_question = llm_provider.generate_vocabulary_question(
                vocab_word_data['word'], 
                context=vocab_word_data['definition']
            )
            vocab_duration = get_latest_llm_timing()
            
            # Log fallback vocabulary question generation individually
            vocab_educational_data = collect_educational_data(
                session_data,
                ChatResponse(response=json.dumps(vocab_question)),
                "storywriting",
                "next_vocabulary_fallback",
                ["vocabulary_question"],
                sub_interaction=1
            )
            latency_logger.log_educational_interaction("vocabulary_question", json.dumps(vocab_question), vocab_duration, vocab_educational_data)
            
            return ChatResponse(
                response=content_manager.get_bot_response("vocabulary.next_question"),
                vocabQuestion=VocabQuestion(**vocab_question),
                sessionData=session_data
            )
    
    # If no more vocabulary words available, finish vocabulary phase
    logger.info("No more vocabulary words available, finishing vocabulary phase")
    return await handle_finish_vocabulary(session_data)

async def handle_finish_vocabulary(session_data: SessionData) -> ChatResponse:
    """Finish vocabulary phase and ask for new story"""
    logger.info("Finishing vocabulary phase, asking for new story")
    
    # Mark vocabulary phase as complete
    session_data.vocabularyPhase.isComplete = True
    session_data.vocabularyPhase.isActive = False
    session_data.awaiting_story_confirmation = True
    
    story_completion_prompt = prompt_manager.get_story_completion_prompt()
    
    return ChatResponse(
        response=story_completion_prompt,
        sessionData=session_data
    )

async def handle_funfacts(user_message: str, session_data: SessionData) -> ChatResponse:
    """Handle fun facts mode interactions"""
    
    # If no topic is set, user is choosing a topic
    if not session_data.topic:
        # Extract topic from user message
        topic = extract_topic_from_message(user_message)
        session_data.topic = topic
        session_data.factsShown = 0
        session_data.contentVocabulary = []  # Reset content vocabulary for new topic
        
        # Generate first fact with vocabulary integration using external prompt system
        base_prompt = prompt_manager.get_first_fact_prompt(topic)
        enhanced_prompt, selected_vocab = prompt_manager.enhance_with_vocabulary(
            base_prompt, topic, session_data.askedVocabWords
        )
        fact_response = llm_provider.generate_response(enhanced_prompt, system_prompt=llm_provider.fun_facts_system_prompt)
        session_data.currentFact = fact_response
        session_data.allFacts.append(fact_response)
        session_data.factsShown += 1
        
        # Track vocabulary words that were intended to be used
        if selected_vocab:
            logger.info(f"Fun fact generation included vocabulary: {selected_vocab}")
            session_data.contentVocabulary.extend(selected_vocab)
            logger.info(f"📋 VOCABULARY TRACKING: Added {len(selected_vocab)} words to session. Total tracked: {len(session_data.contentVocabulary)}")
        
        # Log vocabulary debug info to server logs
        log_vocabulary_debug_info(
            topic, session_data.askedVocabWords, fact_response, "Initial Fun Fact", len(session_data.contentVocabulary)
        )
        
        # Generate vocabulary question using content-based extraction
        fact_vocab_words = extract_vocabulary_from_content(fact_response, session_data.contentVocabulary)
        vocab_question = None
        
        # Find a vocabulary word that hasn't been asked yet from the fact content
        available_fact_words = [word for word in fact_vocab_words if word not in session_data.askedVocabWords]
        
        if available_fact_words:
            selected_word = select_best_vocabulary_word(available_fact_words)
            session_data.askedVocabWords.append(selected_word)
            
            # Use the actual fact content as context for the vocabulary question
            vocab_question = llm_provider.generate_vocabulary_question(selected_word, context=fact_response)
        else:
            # Fallback to curated vocabulary if no words found in fact content
            vocab_word_data = vocabulary_manager.select_vocabulary_word(
                topic=topic,
                used_words=session_data.askedVocabWords
            )
            if vocab_word_data:
                session_data.askedVocabWords.append(vocab_word_data['word'])
                vocab_question = llm_provider.generate_vocabulary_question(
                    vocab_word_data['word'], 
                    context=vocab_word_data['definition']
                )
        
        # Get theme suggestion for this topic
        suggested_theme = get_theme_suggestion(topic)
        
        
        return ChatResponse(
            response=fact_response,
            vocabQuestion=VocabQuestion(**vocab_question) if vocab_question else None,
            sessionData=session_data,
            suggestedTheme=suggested_theme
        )
    
    # Topic is set, continue with more facts
    else:
        if session_data.factsShown < 3:  # Show 3 facts per topic
            # Generate another fact with vocabulary integration using external prompt system
            previous_facts = " | ".join(session_data.allFacts) if session_data.allFacts else "None"
            base_prompt = prompt_manager.get_continuing_fact_prompt(
                session_data.topic, 
                session_data.factsShown + 1,
                previous_facts
            )
            enhanced_prompt, selected_vocab = prompt_manager.enhance_with_vocabulary(
                base_prompt, session_data.topic, session_data.askedVocabWords + session_data.contentVocabulary
            )
            fact_response = llm_provider.generate_response(enhanced_prompt, system_prompt=llm_provider.fun_facts_system_prompt)
            session_data.currentFact = fact_response
            session_data.allFacts.append(fact_response)
            session_data.factsShown += 1
            
            # Track vocabulary words that were intended to be used
            if selected_vocab:
                logger.info(f"Continuing fun fact included vocabulary: {selected_vocab}")
                session_data.contentVocabulary.extend(selected_vocab)
                logger.info(f"📋 VOCABULARY TRACKING: Added {len(selected_vocab)} words to session. Total tracked: {len(session_data.contentVocabulary)}")
            
            # Log vocabulary debug info to server logs
            log_vocabulary_debug_info(
                session_data.topic, session_data.askedVocabWords, fact_response, "Continuing Fun Fact", len(session_data.contentVocabulary)
            )
            
            # Generate vocabulary question using content-based extraction
            fact_vocab_words = extract_vocabulary_from_content(fact_response, session_data.contentVocabulary)
            vocab_question = None
            
            # Find a vocabulary word that hasn't been asked yet from the fact content
            available_fact_words = [word for word in fact_vocab_words if word not in session_data.askedVocabWords]
            
            if available_fact_words:
                selected_word = select_best_vocabulary_word(available_fact_words)
                session_data.askedVocabWords.append(selected_word)
                
                # Use the actual fact content as context for the vocabulary question
                vocab_question = llm_provider.generate_vocabulary_question(selected_word, context=fact_response)
            else:
                # Fallback to curated vocabulary if no words found in fact content
                vocab_word_data = vocabulary_manager.select_vocabulary_word(
                    topic=session_data.topic,
                    used_words=session_data.askedVocabWords
                )
                if vocab_word_data:
                    session_data.askedVocabWords.append(vocab_word_data['word'])
                    vocab_question = llm_provider.generate_vocabulary_question(
                        vocab_word_data['word'], 
                        context=vocab_word_data['definition']
                    )
            
            return ChatResponse(
                response=fact_response,
                vocabQuestion=VocabQuestion(**vocab_question) if vocab_question else None,
                sessionData=session_data
            )
        else:
            # Check if user wants to switch to a new topic
            # Don't extract topic from "continue" messages - these are continuation signals, not topic changes
            message_lower = user_message.lower().strip()
            if message_lower == "continue":
                new_topic = None  # Ignore topic extraction for continue signals
                logger.info(f"User requested continue - no topic change")
            elif message_lower in ["same topic", "same", "more", "keep going", "this topic"]:
                new_topic = session_data.topic  # Explicitly set to current topic for same-topic continuation
                logger.info(f"User requested same topic continuation - current topic: {session_data.topic}")
            else:
                new_topic = extract_topic_from_message(user_message)
                logger.info(f"Extracted topic from message '{user_message}': {new_topic}")
                logger.info(f"Current session topic: {session_data.topic}")
            
            # Check if user wants to continue with same topic or switch to new topic
            if new_topic and new_topic == session_data.topic:
                # User wants to continue with same topic - reset for more facts
                session_data.factsShown = 0
                session_data.allFacts = []
                session_data.currentFact = None
                session_data.askedVocabWords = []  # Reset vocabulary words for fresh start
                session_data.contentVocabulary = []  # Reset content vocabulary for fresh start
                
                # Generate first fact for continuing topic using external prompt system
                base_prompt = prompt_manager.get_new_topic_fact_prompt(session_data.topic)
                enhanced_prompt, selected_vocab = prompt_manager.enhance_with_vocabulary(
                    base_prompt, session_data.topic, session_data.askedVocabWords
                )
                logger.info(f"Continuing same topic '{session_data.topic}' - generated prompt: {enhanced_prompt[:200]}...")
                fact_response = llm_provider.generate_response(enhanced_prompt, system_prompt=llm_provider.fun_facts_system_prompt)
                logger.info(f"LLM response for same topic continuation: {fact_response[:100]}...")
                session_data.currentFact = fact_response
                session_data.allFacts.append(fact_response)
                session_data.factsShown += 1
                
                # Track vocabulary words that were intended to be used
                if selected_vocab:
                    logger.info(f"Same topic continuation included vocabulary: {selected_vocab}")
                    session_data.contentVocabulary.extend(selected_vocab)
                    logger.info(f"📋 VOCABULARY TRACKING: Added {len(selected_vocab)} words to session. Total tracked: {len(session_data.contentVocabulary)}")
                
                # Log vocabulary debug info to server logs
                log_vocabulary_debug_info(
                    session_data.topic, session_data.askedVocabWords, fact_response, "Same Topic Continuation", len(session_data.contentVocabulary)
                )
                
                # Generate vocabulary question using content-based extraction
                fact_vocab_words = extract_vocabulary_from_content(fact_response, session_data.contentVocabulary)
                vocab_question = None
                
                # Find a vocabulary word that hasn't been asked yet from the fact content
                available_fact_words = [word for word in fact_vocab_words if word not in session_data.askedVocabWords]
                
                if available_fact_words:
                    selected_word = select_best_vocabulary_word(available_fact_words)
                    session_data.askedVocabWords.append(selected_word)
                    
                    # Use the actual fact content as context for the vocabulary question
                    vocab_question = llm_provider.generate_vocabulary_question(selected_word, context=fact_response)
                else:
                    # Fallback to curated vocabulary if no words found in fact content
                    vocab_word_data = vocabulary_manager.select_vocabulary_word(
                        topic=session_data.topic,
                        used_words=session_data.askedVocabWords
                    )
                    if vocab_word_data:
                        session_data.askedVocabWords.append(vocab_word_data['word'])
                        vocab_question = llm_provider.generate_vocabulary_question(
                            vocab_word_data['word'], 
                            context=vocab_word_data['definition']
                        )
                
                return ChatResponse(
                    response=f"Great! Let's continue with more {session_data.topic} facts!\n\n{fact_response}",
                    vocabQuestion=VocabQuestion(**vocab_question) if vocab_question else None,
                    sessionData=session_data
                )
            elif new_topic and new_topic != session_data.topic:
                # Reset session state for new topic
                session_data.topic = new_topic
                session_data.factsShown = 0
                session_data.allFacts = []
                session_data.currentFact = None
                session_data.askedVocabWords = []  # Reset vocabulary words for new topic
                session_data.contentVocabulary = []  # Reset content vocabulary for new topic
                
                # Generate first fact for new topic with vocabulary integration using external prompt system
                base_prompt = prompt_manager.get_new_topic_fact_prompt(new_topic)
                enhanced_prompt, selected_vocab = prompt_manager.enhance_with_vocabulary(
                    base_prompt, new_topic, session_data.askedVocabWords
                )
                fact_response = llm_provider.generate_response(enhanced_prompt, system_prompt=llm_provider.fun_facts_system_prompt)
                session_data.currentFact = fact_response
                session_data.allFacts.append(fact_response)
                session_data.factsShown += 1
                
                # Track vocabulary words that were intended to be used
                if selected_vocab:
                    logger.info(f"New topic fun fact included vocabulary: {selected_vocab}")
                    session_data.contentVocabulary.extend(selected_vocab)
                    logger.info(f"📋 VOCABULARY TRACKING: Added {len(selected_vocab)} words to session. Total tracked: {len(session_data.contentVocabulary)}")
                
                # Log vocabulary debug info to server logs
                log_vocabulary_debug_info(
                    new_topic, session_data.askedVocabWords, fact_response, "New Topic Fun Fact", len(session_data.contentVocabulary)
                )
                
                # Generate vocabulary question using content-based extraction
                fact_vocab_words = extract_vocabulary_from_content(fact_response, session_data.contentVocabulary)
                vocab_question = None
                
                # Find a vocabulary word that hasn't been asked yet from the fact content
                available_fact_words = [word for word in fact_vocab_words if word not in session_data.askedVocabWords]
                
                if available_fact_words:
                    selected_word = select_best_vocabulary_word(available_fact_words)
                    session_data.askedVocabWords.append(selected_word)
                    
                    # Use the actual fact content as context for the vocabulary question
                    vocab_question = llm_provider.generate_vocabulary_question(selected_word, context=fact_response)
                else:
                    # Fallback to curated vocabulary if no words found in fact content
                    vocab_word_data = vocabulary_manager.select_vocabulary_word(
                        topic=new_topic,
                        used_words=session_data.askedVocabWords
                    )
                    if vocab_word_data:
                        session_data.askedVocabWords.append(vocab_word_data['word'])
                        vocab_question = llm_provider.generate_vocabulary_question(
                            vocab_word_data['word'], 
                            context=vocab_word_data['definition']
                        )
                
                # Get theme suggestion for new topic
                suggested_theme = get_theme_suggestion(new_topic)
                
                return ChatResponse(
                    response=fact_response,
                    vocabQuestion=VocabQuestion(**vocab_question) if vocab_question else None,
                    sessionData=session_data,
                    suggestedTheme=suggested_theme
                )
            else:
                # No new topic detected, ask if they want to switch topics
                topic_name = session_data.topic if session_data.topic else "general"
                return ChatResponse(
                    response=f"We've explored some great {topic_name} facts! Would you like to learn about a different topic? Try animals, space, inventions, or something else!",
                    sessionData=session_data
                )

# Legacy function removed - now using vocabulary_manager.select_vocabulary_word()

def extract_topic_from_message(message: str) -> str:
    """Extract topic from user message using centralized config"""
    message_lower = message.lower()
    
    # Use centralized topic keywords from config
    for topic, keywords in THEME_CONFIG['topicKeywords'].items():
        if any(keyword in message_lower for keyword in keywords):
            return topic
    
    # Default topic extraction - use first word that looks like a topic
    words = message.split()
    return words[0] if words else "adventure"

def get_theme_suggestion(topic: str) -> str:
    """Map topic to theme suggestion for frontend using centralized config"""
    return THEME_CONFIG['themeMapping'].get(topic.lower(), THEME_CONFIG['defaultTheme'])

# Health check endpoint
@app.get("/health")
async def health_check():
    return {"status": "healthy", "message": "English Learning Chatbot API is running!"}

# Mount static files from frontend directory AFTER all API routes
import os
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/", StaticFiles(directory=frontend_path, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
