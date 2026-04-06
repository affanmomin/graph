#!/bin/bash
# UserPromptSubmit hook — automatically prepends repo memory context to task prompts.
#
# When .agent-memory/ exists, runs `repomind memory prepare-context` on the
# user's prompt and injects the result so Claude is oriented without the user
# having to type any repomind commands.
#
# Skips injection for short/conversational messages (< 8 words) to avoid
# cluttering follow-up replies like "yes", "continue", "thanks".

MEMORY_DIR=".agent-memory"
MIN_WORDS=8

# Read the prompt from stdin (Claude Code passes it via stdin for UserPromptSubmit)
PROMPT=$(cat)

# Skip if memory hasn't been initialised yet
if [ ! -d "$MEMORY_DIR" ]; then
    exit 0
fi

# Skip short/conversational messages — not worth injecting context for "yes" or "continue"
WORD_COUNT=$(echo "$PROMPT" | wc -w | tr -d ' ')
if [ "$WORD_COUNT" -lt "$MIN_WORDS" ]; then
    exit 0
fi

# Run prepare-context; suppress errors so a broken graph never blocks the user
CONTEXT=$(repomind memory prepare-context "$PROMPT" 2>/dev/null)

if [ -z "$CONTEXT" ]; then
    exit 0
fi

# Output the context block — Claude Code injects this before the user's message
cat <<EOF
[repomind context]
$CONTEXT
[end repomind context]
EOF
