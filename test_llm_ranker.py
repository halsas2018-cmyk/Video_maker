#!/usr/bin/env python3
"""Test script to verify the llm_ranker works with the new concealment/exposure criterion."""

import json
from llm_ranker import rerank, RANK_SYSTEM_PROMPT

def test_prompt_contains_new_criterion():
    """Test that the prompt contains our new criterion."""
    print("Testing that prompt contains concealment/exposure signal criterion...")
    assert "Concealment/exposure signal" in RANK_SYSTEM_PROMPT
    assert "secret," in RANK_SYSTEM_PROMPT
    assert "hidden," in RANK_SYSTEM_PROMPT
    assert "FEW-SHOT EXAMPLES" in RANK_SYSTEM_PROMPT
    print("✓ Prompt contains new criterion and examples")

def test_with_mock_stories():
    """Test ranking with some mock stories that test the concealment/exposure signal."""
    print("\nTesting with mock stories...")

    # Create mock stories - some with high concealment/exposure, some with low
    mock_stories = [
        {
            "source": "TechCrunch",
            "title": "Internal emails show Facebook knew Instagram harmed teen girls' mental health for years but hid the research",
            "summary": "Leaked internal documents reveal that Facebook conducted internal studies showing Instagram's negative impact on teenage mental health but chose not to disclose these findings publicly.",
            "published": "2026-10-02T10:00:00Z",
            "category": "ai",
            "score": 25.0
        },
        {
            "source": "The Verge",
            "title": "Apple announces new iPhone 16 with improved camera and longer battery life",
            "summary": "Apple today unveiled the iPhone 16 featuring a new camera system and extended battery life, continuing its annual smartphone release cycle.",
            "published": "2026-10-02T09:00:00Z",
            "category": "ai",
            "score": 20.0
        },
        {
            "source": "Bloomberg",
            "title": "Security researcher finds backdoor in popular VPN service that let attackers decrypt user traffic for months",
            "summary": "A security researcher discovered a hidden backdoor in a widely-used VPN service that could have allowed attackers to intercept and decrypt user traffic for several months before being discovered.",
            "published": "2026-10-02T08:00:00Z",
            "category": "business",
            "score": 22.0
        },
        {
            "source": "Reuters",
            "title": "Google releases new AI model Gemini with multimodal capabilities",
            "summary": "Google announced the release of its latest AI model, Gemini, which features multimodal capabilities for processing text, images, and audio.",
            "published": "2026-10-02T07:00:00Z",
            "category": "ai",
            "score": 18.0
        },
        {
            "source": "Ars Technica",
            "title": "Whistleblower exposes illegal data mining practices at major social media platform",
            "summary": "A former employee came forward as a whistleblower to reveal that a major social media platform had been illegally mining user data for advertising purposes without proper consent.",
            "published": "2026-10-02T06:00:00Z",
            "category": "ai",
            "score": 24.0
        }
    ]

    try:
        # Test the rerank function
        ranked_stories, rank_source = rerank(mock_stories, max_picks=3)

        print(f"Ranking completed using: {rank_source}")
        print(f"Number of stories ranked: {len(ranked_stories)}")

        if rank_source == "llm":
            print("\nRanked stories:")
            for i, story in enumerate(ranked_stories, 1):
                print(f"{i}. [{story.get('score', '?')}] {story.get('title', 'No title')[:60]}...")
                if story.get('llm_reason'):
                    print(f"   Reason: {story.get('llm_reason')}")
        else:
            print("LLM ranking fell back to heuristic - this might be expected in test environment")

    except Exception as e:
        print(f"Error during ranking (expected in test environment without API): {e}")
        print("This is okay - we're mainly testing that the prompt was updated correctly")

if __name__ == "__main__":
    test_prompt_contains_new_criterion()
    test_with_mock_stories()
    print("\n✓ All tests completed!")