#!/usr/bin/env python3
"""
Test script to demonstrate hook improvement with the new specificity rule.
This shows a case where the original hook is weak (restates title/generic)
and how the improved rule creates a stronger hook.
"""

def test_hook_improvement():
    """Test that demonstrates hook improvement from weak to strong."""

    print("=" * 60)
    print("HOOK IMPROVEMENT TEST")
    print("=" * 60)

    # Example story where original hook might be weak
    story_title = "Apple Security Flaw Allows Remote Code Execution"
    story_source = "TechCrunch"
    story_summary = """
    A critical security vulnerability was discovered in Apple's macOS that
    allows attackers to execute arbitrary code remotely without user
    interaction. The flaw affects all versions of macOS Ventura and later,
    and could allow complete system compromise. Apple has released an
    emergency security update to address the issue.
    """

    print(f"Story Title: {story_title}")
    print(f"Source: {story_source}")
    print(f"Summary: {story_summary.strip()}")
    print()

    # Simulate what a WEAK hook might look like (before our improvements)
    weak_hook = "Apple has a security flaw that allows remote code execution."
    print("WEAK HOOK EXAMPLE (what we want to avoid):")
    print(f"  \"{weak_hook}\"")
    print("  Problems:")
    print("  - Restates/paraphrases the title without adding specificity")
    print("  - Doesn't name the specific vulnerability type or affected versions")
    print("  - No concrete details that make viewer want to know more")
    print()

    # Show what a STRONG hook should look like (after our improvements)
    strong_hook = "A zero-click vulnerability in macOS Ventura lets hackers take complete control of your Mac just by sending a malicious image."
    print("STRONG HOOK EXAMPLE (what our rule encourages):")
    print(f"  \"{strong_hook}\"")
    print("  Improvements:")
    print("  - Names the specific vulnerability type: 'zero-click vulnerability'")
    print("  - Specifies the affected system: 'macOS Ventura'")
    print("  - Reveals the attack vector: 'just by sending a malicious image'")
    print("  - Shows the consequence: 'take complete control of your Mac'")
    print("  - Does NOT restate the title - assumes viewer already read it")
    print("  - Provides specific, surprising detail that creates curiosity")
    print()

    # Another example with a company/platform name
    story_title_2 = "Meta's New AI Model Trained on User Data Without Consent"
    story_source_2 = "The Verge"
    story_summary_2 = """
    Internal documents reveal that Meta used private user data from
    Facebook and Instagram to train its latest AI language model,
    despite users explicitly opting out of data sharing for research
    purposes. The training data included private messages, photos,
    and location information from millions of users who had disabled
    data sharing in their privacy settings.
    """

    print("-" * 60)
    print(f"Story Title 2: {story_title_2}")
    print(f"Source 2: {story_source_2}")
    print(f"Summary 2: {story_summary_2.strip()}")
    print()

    # Weak hook example
    weak_hook_2 = "Meta used user data to train AI without permission."
    print("WEAK HOOK EXAMPLE 2:")
    print(f"  \"{weak_hook_2}\"")
    print("  Problems:")
    print("  - Very generic, replaces specific details with vague terms")
    print("  - Doesn't mention which platforms or what type of data")
    print("  - No specifics about the scale or nature of the violation")
    print()

    # Strong hook example
    strong_hook_2 = "Meta trained its AI on millions of users' private Facebook messages and Instagram photos despite explicit opt-out settings."
    print("STRONG HOOK EXAMPLE 2:")
    print(f"  \"{strong_hook_2}\"")
    print("  Improvements:")
    print("  - Keeps concrete platform names: 'Facebook', 'Instagram'")
    print("  - Specifies the data types: 'private messages and Instagram photos'")
    print("  - Mentions the scale: 'millions of users'")
    print("  - Notes the violation: 'despite explicit opt-out settings'")
    print("  - Does NOT replace 'Facebook' with 'a social platform' or 'Instagram' with 'a photo app'")
    print("  - Specificity increased, not decreased")
    print()

    print("=" * 60)
    print("KEY TAKEAWAYS FROM SPECIFICITY RULE:")
    print("=" * 60)
    print("✅ NEVER replace 'Hugging Face' with 'a public platform'")
    print("✅ NEVER replace 'Meta' with 'a tech company'")
    print("✅ NEVER replace 'macOS Ventura' with 'an operating system'")
    print("✅ NEVER replace 'Facebook messages' with 'user communications'")
    print("✅ ALWAYS keep or increase specificity when possible")
    print("✅ Concrete names/numbers create stronger hooks and better retention")
    print()
    print("The hook rule now ensures specificity is preserved or increased,")
    print("which makes hooks more surprising, specific, and engaging.")

if __name__ == "__main__":
    test_hook_improvement()