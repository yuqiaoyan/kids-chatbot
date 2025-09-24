"""Storywriting service handling multi-step story interactions."""

import json
import logging

from content_manager import content_manager
from llm_provider import llm_provider
from prompt_manager import prompt_manager
from telemetry import collect_educational_data, get_latest_llm_timing, latency_logger
from utils import extract_topic_from_message, get_theme_suggestion
from vocabulary_manager import vocabulary_manager

from .design_service import (
    handle_design_phase_interaction,
    trigger_design_phase,
    trigger_enhanced_design_phase,
    validate_entity_structure,
)
from .story_parser import parse_enhanced_story_response, parse_structured_story_response
from .vocabulary_service import (
    extract_vocabulary_from_content,
    log_vocabulary_debug_info,
    select_advanced_general_words,
    select_best_vocabulary_word,
)
from models.schemas import ChatResponse, SessionData, VocabQuestion, VocabularyPhase


logger = logging.getLogger(__name__)


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


__all__ = [
    "handle_finish_vocabulary",
    "handle_next_vocabulary",
    "handle_start_vocabulary",
    "handle_storywriting",
]
