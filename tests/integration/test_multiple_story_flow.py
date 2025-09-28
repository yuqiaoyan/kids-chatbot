"""
End-to-end integration test for multiple story flow
Tests the complete journey: Story 1 → Vocabulary → Story 2 → Design Phase

This test prevents regressions like:
- create_enhanced_story_prompt() not defined error
- Design phase not triggering for second stories
- Session field contamination between stories
"""

import sys
import os
import pytest
from unittest.mock import Mock, patch
import json

# Add backend to path for imports and set working directory
backend_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'backend')
sys.path.append(backend_dir)

# Change working directory to backend for file loading
original_cwd = os.getcwd()
os.chdir(backend_dir)

from app import handle_storywriting, SessionData, VocabularyPhase
from llm_provider import LLMProvider

# Restore original working directory
os.chdir(original_cwd)

class TestMultipleStoryFlow:
    """Test complete flow from story 1 through story 2 with design phases"""
    
    def setup_method(self):
        """Set up fresh session for each test"""
        self.session_data = SessionData()
        
    @pytest.fixture
    def mock_llm_responses(self):
        """Mock LLM responses for deterministic testing"""
        return {
            # First story generation with unnamed character
            "space_story_1": {
                "story": "In a distant galaxy, a **curious** young explorer discovered a mysterious **ancient** spaceship.",
                "entities": {
                    "characters": {
                        "named": [],
                        "unnamed": ["young explorer"]
                    },
                    "locations": {
                        "named": [],
                        "unnamed": ["mysterious spaceship"]
                    }
                },
                "vocabulary_words": ["curious", "ancient"]
            },
            
            # Story continuation
            "story_continuation": "The explorer carefully approached the glowing ship, wondering what **incredible** secrets it might hold.",
            
            # Vocabulary question
            "vocabulary_question": "What does 'curious' mean in this story?\nA) Sleepy\nB) Wanting to learn or know something\nC) Scared\nD) Hungry",
            
            # Second story generation with design-eligible entities
            "food_story_2": {
                "story": "In a magical kitchen, a **talented** chef discovered a **mysterious** cookbook that could create any dish imaginable.",
                "entities": {
                    "characters": {
                        "named": [],
                        "unnamed": ["talented chef"]
                    },
                    "locations": {
                        "named": [],
                        "unnamed": ["magical kitchen"]
                    }
                },
                "vocabulary_words": ["talented", "mysterious"]
            },
            
            # Design phase continuation after naming
            "design_continuation": "Now that we know the chef is named Luna, let's continue our magical cooking adventure! Luna opened the mysterious cookbook and found a recipe for **enchanted** cookies that could grant wishes."
        }
    
    @patch('llm_provider.llm_provider.generate_response')
    def test_complete_multiple_story_flow(self, mock_generate, mock_llm_responses):
        """Test complete flow: Story 1 → Vocabulary → Story 2 → Design Phase"""
        
        # Configure mock to return different responses based on call order
        mock_generate.side_effect = [
            json.dumps(mock_llm_responses["space_story_1"]),  # First story
            mock_llm_responses["story_continuation"],          # Story continuation  
            mock_llm_responses["vocabulary_question"],         # Vocabulary question
            json.dumps(mock_llm_responses["food_story_2"]),    # Second story
            mock_llm_responses["design_continuation"]          # Design continuation
        ]
        
        # Step 1: Start first story with space topic
        response1 = handle_storywriting("space adventures", self.session_data)
        
        # Verify first story generates correctly
        assert "curious" in response1.response
        assert "explorer" in response1.response
        assert self.session_data.topic == "space adventures"
        assert len(self.session_data.storyParts) == 1
        
        # Check if design phase was triggered (should be True for unnamed entities)
        design_triggered_1 = hasattr(response1, 'designPrompt') and response1.designPrompt is not None
        
        # Step 2: Complete first story (simulate user interactions)
        self.session_data.currentStep = 6  # Mark story as complete
        self.session_data.isComplete = True
        
        # Step 3: Handle vocabulary phase
        response2 = handle_storywriting("finish_vocabulary", self.session_data)
        
        # Verify vocabulary phase completion
        assert "new story" in response2.response.lower() or "another story" in response2.response.lower()
        assert self.session_data.awaiting_story_confirmation == True
        
        # Step 4: Start second story with food topic
        response3 = handle_storywriting("food adventures", self.session_data)
        
        # CRITICAL: Verify second story generates without "create_enhanced_story_prompt" error
        assert response3.response is not None
        assert len(response3.response) > 0
        assert "having trouble" not in response3.response.lower()
        
        # Verify second story content
        assert "chef" in response3.response or "kitchen" in response3.response
        assert self.session_data.topic == "food adventures"  # Topic updated
        
        # Verify session reset worked correctly
        assert len(self.session_data.storyParts) >= 1  # Should have new story part
        assert self.session_data.isComplete == False    # Should be reset
        assert self.session_data.currentStep == 2       # Should be reset
        
        # CRITICAL: Verify design phase fields were reset
        # These should all be None/empty after reset
        design_fields_reset = (
            self.session_data.designPhase is None and
            self.session_data.currentDesignAspect is None and
            len(self.session_data.designAspectHistory) == 0 and
            self.session_data.designComplete == False and
            self.session_data.namingComplete == False and
            len(self.session_data.designedEntities) == 0 and
            self.session_data.currentEntityType is None and
            self.session_data.currentEntityDescriptor is None and
            self.session_data.currentEntityName is None and
            self.session_data.entityNameOverrides == {}
        )
        
        assert design_fields_reset, "Design phase fields were not properly reset between stories"
        
        # Step 5: Test design phase for second story (if triggered)
        if hasattr(response3, 'designPrompt') and response3.designPrompt is not None:
            # Simulate naming the chef
            response4 = handle_storywriting("Luna", self.session_data)
            
            # Verify naming worked
            assert "Luna" in response4.response
            assert self.session_data.namingComplete == True
            
            # Verify story continuation after design
            if "continue" in response4.response.lower():
                assert len(response4.response) > len("Luna")  # Should have story continuation
    
    @patch('llm_provider.llm_provider.generate_response')
    def test_session_field_isolation(self, mock_generate, mock_llm_responses):
        """Test that session fields are properly isolated between stories"""
        
        mock_generate.side_effect = [
            json.dumps(mock_llm_responses["space_story_1"]),
            json.dumps(mock_llm_responses["food_story_2"])
        ]
        
        # Start first story and simulate design phase state
        response1 = handle_storywriting("space", self.session_data)
        
        # Manually set design phase fields (simulate active design)
        self.session_data.designPhase = "character"
        self.session_data.currentDesignAspect = "naming"
        self.session_data.designAspectHistory = ["naming", "appearance"]
        self.session_data.designComplete = True
        self.session_data.designedEntities = ["young explorer"]
        self.session_data.currentEntityType = "character"
        self.session_data.currentEntityDescriptor = "the young explorer"
        self.session_data.currentEntityName = "Explorer"
        self.session_data.entityNameOverrides = {"the young explorer": "Explorer"}
        
        # Mark first story complete and ready for new story
        self.session_data.isComplete = True
        self.session_data.awaiting_story_confirmation = True
        
        # Start second story
        response2 = handle_storywriting("food adventures", self.session_data)
        
        # Verify all design fields were reset
        assert self.session_data.designPhase is None
        assert self.session_data.currentDesignAspect is None
        assert len(self.session_data.designAspectHistory) == 0
        assert self.session_data.designComplete == False
        assert len(self.session_data.designedEntities) == 0
        assert self.session_data.currentEntityType is None
        assert self.session_data.currentEntityDescriptor is None
        assert self.session_data.currentEntityName is None
        assert self.session_data.entityNameOverrides == {}
        
        # Verify new story started successfully
        assert self.session_data.topic == "food adventures"
        assert self.session_data.isComplete == False
    
    def test_error_handling_in_multiple_stories(self):
        """Test graceful error handling doesn't break multiple story flow"""
        
        # Test that missing function errors are caught
        try:
            # This should not raise "create_enhanced_story_prompt not defined" anymore
            self.session_data.isComplete = True
            self.session_data.awaiting_story_confirmation = True
            
            response = handle_storywriting("test topic", self.session_data)
            
            # Should get some response, not crash
            assert response is not None
            
        except NameError as e:
            if "create_enhanced_story_prompt" in str(e):
                pytest.fail("create_enhanced_story_prompt error still exists - bug not fixed!")
            else:
                raise  # Some other NameError
    
    def test_vocabulary_phase_to_new_story_transition(self):
        """Test specific transition from vocabulary completion to new story"""
        
        # Simulate completed first story in vocabulary phase
        self.session_data.topic = "space"
        self.session_data.isComplete = True
        self.session_data.vocabularyPhase.isComplete = True
        
        # Test finish_vocabulary command
        response = handle_storywriting("finish_vocabulary", self.session_data)
        
        # Should ask for new story
        assert self.session_data.awaiting_story_confirmation == True
        assert "story" in response.response.lower()

