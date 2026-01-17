#!/usr/bin/env python3
"""Comprehensive test for all Taostats templates"""

import asyncio
import sys
sys.path.insert(0, '.')

from liveweb_arena.plugins.taostats.templates import (
    SubnetInfoTemplate,
    NetworkTemplate,
    PriceTemplate,
    ComparisonTemplate,
    AccountTemplate,
    TransferTemplate,
    AnalysisTemplate,
)


async def test_template(template, name: str, seeds: list, mock_answers: dict = None):
    """Test a template with multiple seeds"""
    print(f"\n{'='*60}")
    print(f"Testing: {name}")
    print('='*60)

    results = []
    for seed in seeds:
        q = template.generate(seed)
        print(f"\n[Seed {seed}]")
        print(f"  Question: {q.question_text[:80]}...")

        # Get ground truth
        gt = await template.get_ground_truth(q.validation_info)
        if gt is None:
            print(f"  ❌ Ground truth: None (FAILED)")
            results.append(False)
            continue

        print(f"  ✅ Ground truth: {gt}")

        # Test validation with correct answer
        if mock_answers and seed in mock_answers:
            answer = mock_answers[seed]
        else:
            # Use ground truth as answer for validation test
            answer = str(gt) if not isinstance(gt, tuple) else gt[0]

        validation = await template.validate_answer(answer, q.validation_info)
        print(f"  Validation test (answer='{answer[:50]}...' if len(answer) > 50 else answer):")
        print(f"    Score: {validation.score}")
        print(f"    Expected: {validation.expected}")
        print(f"    Details: {validation.details}")

        if validation.score >= 0.5:
            print(f"  ✅ Validation passed")
            results.append(True)
        else:
            print(f"  ❌ Validation failed")
            results.append(False)

    passed = sum(results)
    total = len(results)
    print(f"\n{name}: {passed}/{total} tests passed")
    return passed == total


async def main():
    print("="*60)
    print("TAOSTATS TEMPLATE COMPREHENSIVE TEST")
    print("="*60)

    all_passed = True

    # 1. Test SubnetInfoTemplate
    t = SubnetInfoTemplate()
    passed = await test_template(t, "SubnetInfoTemplate", [1000, 1001, 1002])
    all_passed = all_passed and passed

    # 2. Test NetworkTemplate
    t = NetworkTemplate()
    passed = await test_template(t, "NetworkTemplate", [1000, 1001, 1002])
    all_passed = all_passed and passed

    # 3. Test PriceTemplate
    t = PriceTemplate()
    # Mock answers that look like real prices
    mock = {1000: "$270", 1001: "270.5 USD", 1002: "TAO is $270"}
    passed = await test_template(t, "PriceTemplate", [1000, 1001, 1002], mock)
    all_passed = all_passed and passed

    # 4. Test ComparisonTemplate
    t = ComparisonTemplate()
    passed = await test_template(t, "ComparisonTemplate", [1000, 1001, 1002])
    all_passed = all_passed and passed

    # 5. Test AccountTemplate
    t = AccountTemplate()
    passed = await test_template(t, "AccountTemplate", [1000, 1001, 1002])
    all_passed = all_passed and passed

    # 6. Test TransferTemplate
    t = TransferTemplate()
    passed = await test_template(t, "TransferTemplate", [1000, 1001])
    all_passed = all_passed and passed

    # 7. Test AnalysisTemplate
    t = AnalysisTemplate()
    passed = await test_template(t, "AnalysisTemplate", [1000, 1001, 1002])
    all_passed = all_passed and passed

    print("\n" + "="*60)
    if all_passed:
        print("✅ ALL TESTS PASSED")
    else:
        print("❌ SOME TESTS FAILED")
    print("="*60)

    return all_passed


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
