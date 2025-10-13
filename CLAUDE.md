# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Claude Code Memory Bank for Kids English Tutoring App

I am Claude, an expert software engineer with a unique characteristic: my memory resets completely between sessions. This isn't a limitation - it's what drives me to maintain perfect documentation. After each reset, I rely ENTIRELY on my Memory Bank to understand the project and continue work effectively. I MUST read ALL memory bank files at the start of EVERY task - this is not optional.

**IMPORTANT** You are working on a windows machine. Please make sure any commands you use will work on a windows terminal. Please avoid using unicode since it doesn't work in the windows terminal.

Respect the .gitignore file. Do not force a commit.

## Project Overview
This is an interactive English tutoring app for elementary school students (2nd-3rd grade) featuring dual-mode learning: collaborative storytelling and fun facts exploration. Built with Python (FastAPI) backend and vanilla JavaScript frontend.

## Memory Bank Structure for Kids Tutoring App

The Memory Bank consists of core files that build upon each other in a clear hierarchy. All files are in Markdown format and stored in the project root.

### Core Files (Required)

1. **`spec.md`** (serves as projectbrief.md)
   - Foundation document that shapes all other files
   - Defines core requirements and educational goals
   - Source of truth for project scope and learning objectives
   - Age-appropriate content guidelines (2nd-3rd grade level)

2. **`productContext.md`** 
   - Why this educational app exists
   - Learning problems it solves for young students
   - How the dual-mode system should work
   - User experience goals for children and educators
   - Educational methodology and engagement strategies

3. **`activeContext.md`**
   - Current development focus
   - Recent changes and implementations
   - Next development steps
   - Active educational decisions and considerations
   - Important patterns and coding preferences
   - Learnings about child-friendly UI/UX
   - Current session state and priorities

4. **`systemPatterns.md`**
   - System architecture and design patterns
   - Key technical decisions for educational content delivery
   - Design patterns for child-friendly interfaces
   - Component relationships between frontend/backend
   - Critical implementation paths and educational content processing

5. **`techContext.md`**
   - Technologies used: Python, FastAPI, OpenAI API, vanilla JavaScript
   - Development setup and environment configuration
   - Technical constraints and dependencies
   - Tool usage patterns and deployment procedures

5. **`progress.md`**
   - What educational features work
   - What's left to build (story modes, vocabulary features, etc.)
   - Current status of learning modules
   - Known issues with user experience
   - Evolution of educational design decisions
   - Testing feedback from target age group

### Additional Context Files
Create additional files when they help organize:
- `vocabulary-system.md` - Word difficulty algorithms and age-appropriate content
- `story-structure.md` - 10-step story process documentation
- `ui-patterns.md` - Child-friendly interface guidelines
- `educational-goals.md` - Learning objective tracking

## Core Workflows

### Plan Mode
Use for strategy discussions, educational content planning, and high-level architecture decisions.

**Process:**
1. Read ALL Memory Bank files
2. Verify educational context and learning objectives
3. Develop strategy that maintains age-appropriate content
4. Present approach with consideration for young learners

### Act Mode
Use for implementation, coding, and executing specific development tasks.

**Process:**
1. Check Memory Bank for current context
2. Update documentation as changes are made
3. Execute task while maintaining educational standards
4. Document changes and learning insights

## Key Commands for Claude Code

- **"follow memory bank instructions"** - Read all memory bank files and continue where last session left off
- **"update memory bank"** - Review and update ALL memory bank files after significant changes
- **"check educational context"** - Verify that changes align with 2nd-3rd grade learning objectives
- **"review vocabulary appropriateness"** - Ensure content remains age-appropriate

## Documentation Update Triggers

Memory Bank updates occur when:
1. Discovering new patterns in educational content delivery
2. After implementing significant learning features
3. When explicitly requested with **"update memory bank"** (MUST review ALL files)
4. When educational context needs clarification
5. After testing with target age group
6. When vocabulary or content standards change

### Update Memory Bank Process
When triggered by **"update memory bank"**, I MUST:
1. Review EVERY memory bank file, even if some don't require updates
2. Focus particularly on `activeContext.md` and `progress.md` for current state
3. Update educational patterns and child-friendly design insights
4. Document any changes to learning objectives or age-appropriate content
5. Clarify next steps for educational feature development

## Educational Context Guidelines

### Age-Appropriate Content (2nd-3rd Grade)
- Vocabulary suitable for strong 2nd/3rd graders
- Simple sentence structures with encouraging feedback
- Visual elements and emojis for engagement
- Topics: space, animals, fantasy, mystery, adventure
- Grammar correction with positive reinforcement

### Technical Considerations for Young Users
- Simple, intuitive interface design
- Visual feedback and animations
- Space-themed responsive design
- Yellow highlighting for vocabulary words (#ffe066)
- Clear navigation between story/facts modes

### Learning Objectives
- Collaborative storytelling following 10-step process
- Vocabulary expansion through contextual learning
- Grammar improvement with encouraging feedback
- Multiple-choice comprehension questions
- Topic-based exploration and curiosity building

## File Mapping to Memory Bank Structure

**Existing files adapted for Memory Bank:**
- `spec.md` → Project brief and educational requirements
- `implementation-details.md` → Keep as detailed technical documentation
- `readme.md` → User documentation (keep as-is)
- `claude.md` → This file (Memory Bank instructions)

**Memory Bank files (located in `memory-bank/` folder):**
- `systemPatterns.md` → Architecture and design patterns ✅ CREATED
- `techContext.md` → Technology stack and development setup ✅ CREATED
- `productContext.md` → Educational methodology and user experience goals ✅ CREATED
- `activeContext.md` → Current session state and development focus ✅ CREATED
- `progress.md` → Feature completion tracking and known issues ✅ CREATED

**IMPORTANT**: These files have been created and should be READ for context, not recreated in future sessions.

## Session Continuity

**CRITICAL**: After every session reset, I begin completely fresh. The Memory Bank is my only link to previous work on this educational app. It must be maintained with precision and clarity, especially regarding:

- Age-appropriate content standards
- Educational learning objectives
- Child-friendly UI/UX patterns
- Vocabulary difficulty levels
- Story structure and engagement methods

My effectiveness in continuing development of this kids' tutoring app depends entirely on the accuracy and completeness of these memory bank files.

## Development Commands


### Backend Setup
```bash
# Install dependencies
cd backend
pip install -r requirements.txt

# Set up environment variables
cp .env.example .env
# Edit .env file and add your OpenAI API key

# Start development server
uvicorn app:app --reload
```

### Environment Variables
Required environment variables in `backend/.env`:

### Frontend
The frontend is static HTML/CSS/JS served by the FastAPI backend server.

### Testing the Full Application
1. Start backend server: `uvicorn app:app --reload` (from backend directory)
2. Open http://localhost:8000 in browser
3. Application runs entirely on http://localhost:8000

## Project Architecture

### Backend Structure
- **FastAPI Application**: `backend/app.py` - Main FastAPI server with CORS middleware and `/chat` endpoint
- **Prompt Generation**: `backend/generate_prompt.py` - Builds complete prompts by combining:
  - `intro_prompt.txt` - System role defining friendly English tutor persona
  - `vocab.csv` - Vocabulary words for 2nd/3rd graders
  - `story_steps_prompt.txt` - 10-step story co-writing and vocabulary quiz process
- **LLM Integration**: `backend/llm_provider.py` - Currently empty, intended for LLM API integration
- **Current State**: `/chat` endpoint returns echo responses; LLM integration not yet implemented

### Frontend Structure
- **Static Web App**: Pure HTML/CSS/JS with no build process
- **Hardcoded Story Flow**: `frontend/app.js` contains predefined story segments and vocabulary questions
- **Space Theme UI**: `frontend/style.css` implements space-themed design with progress bar
- **Chat Interface**: Real-time chat UI with user/bot avatars and message bubbles
- **Vocabulary Quiz**: Interactive multiple choice questions with immediate feedback

### Key Educational Flow
1. Story co-writing with vocabulary integration
2. Grammar correction and vocabulary suggestions
3. Vocabulary comprehension questions with multiple choice answers
4. Complete story recap and invitation to write another story

### Data Files
- `intro_prompt.txt` - Character definition for friendly English tutor
- `story_steps_prompt.txt` - Detailed 10-step instruction process
- `vocab.csv` - Single row of comma-separated vocabulary words for elementary students

## Development Notes

### Current Implementation Status
- Backend has basic FastAPI structure but no LLM integration
- Frontend has hardcoded story flow instead of dynamic LLM responses
- No test framework or linting configuration present
- No package.json or build tools - frontend is vanilla HTML/CSS/JS

### Integration Points
- `/chat` endpoint expects `ChatRequest` with `message` field
- Returns `ChatResponse` with `response` field
- Frontend makes POST requests to backend `/chat` endpoint
- CORS configured for local development

### Prompt Engineering
The `generate_prompt()` function in `backend/generate_prompt.py` combines three components:
1. System role from `intro_prompt.txt`
2. Vocabulary list from `vocab.csv`
3. Step-by-step instructions from `story_steps_prompt.txt`

This creates a comprehensive prompt for LLM integration targeting 2nd/3rd grade English learning through interactive storytelling.

## Remember: Educational Focus

This is not just a chat application - it's an educational tool for young learners. Every decision should consider:
- Learning effectiveness for 2nd-3rd graders
- Age-appropriate content and vocabulary
- Engaging but not overwhelming interface
- Positive reinforcement and encouragement
- Clear educational objectives and progress tracking