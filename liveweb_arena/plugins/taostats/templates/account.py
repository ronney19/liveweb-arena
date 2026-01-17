"""Account query template for Taostats"""

import random
from typing import Any, Dict, List, Optional

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)


# Known addresses with verifiable balances
# Format: (address, description)
KNOWN_ADDRESSES = [
    ("5GcCZ2BPXBjgG88tXJCEtkbdg2hNrPbL4EFfbiVRvBZdSQDC", "Taostats validator"),
    ("5HCFWvRqzSHWRPecN7q8J6c7aKQnrCZTMHstPv39xL1wgDHh", "Apex subnet owner"),
    ("5DvTpiniW9s3APmHRYn8FroUWyfnLtrsid5Mtn5EwMXHN2ed", "OTF wallet"),
]


@register_template("taostats_account")
class AccountTemplate(QuestionTemplate):
    """
    Template for account balance queries.

    Tests AI's ability to search for and find specific account information.
    Ground truth is fetched from Bittensor SDK.
    """

    PATTERNS: List[str] = [
        "Search for address {address} on taostats.io and find its free balance.",
        "Go to taostats.io and look up the balance of {address}.",
        "What is the free balance of wallet {address}? Search on taostats.io.",
    ]

    def __init__(self):
        super().__init__("taostats_account")

    def generate(self, seed: int) -> GeneratedQuestion:
        rng = random.Random(seed)

        address, description = rng.choice(KNOWN_ADDRESSES)
        pattern = rng.choice(self.PATTERNS)

        # Use truncated address in question to make it searchable
        short_addr = f"{address[:8]}...{address[-6:]}"
        question_text = pattern.format(address=short_addr)

        validation_info = {
            "address": address,
            "short_address": short_addr,
            "description": description,
        }

        return GeneratedQuestion(
            question_text=question_text,
            start_url="https://taostats.io",
            variables={"address": address},
            validation_info=validation_info,
            template_name=self.name,
        )

    def get_validation_rules(self, validation_info: Dict[str, Any]) -> str:
        short_addr = validation_info.get("short_address", "")
        return f"""Task-Specific Rules (Account Balance: {short_addr}):
- Score 1.0: Agent provides balance within 20% of actual (balances change frequently)
- Score 0.5: Agent provides a balance in correct order of magnitude
- Score 0.0: No balance found, wrong format, or wildly incorrect"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> Optional[float]:
        """Fetch account balance from Bittensor SDK"""
        try:
            import bittensor as bt

            address = validation_info.get("address", "")
            if not address:
                return None

            subtensor = bt.Subtensor(network="finney")
            balance = subtensor.get_balance(address)

            return float(balance.tao) if balance else None

        except Exception:
            return None

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate balance answer"""
        import re

        ground_truth = await self.get_ground_truth(validation_info)

        if ground_truth is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details="Ground truth unavailable",
            )

        # Extract number from answer
        # Handle formats like: 0.5 TAO, τ0.5, 0.5
        clean_answer = answer.replace(',', '').replace('τ', '').replace('TAO', '')
        numbers = re.findall(r'[\d.]+', clean_answer)

        agent_balance = None
        for n in numbers:
            try:
                bal = float(n)
                # Sanity check - balance should be in reasonable range
                if 0 <= bal < 1000000000:
                    agent_balance = bal
                    break
            except ValueError:
                continue

        if agent_balance is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=f"τ{ground_truth:.6f}",
                actual=answer,
                details="No valid balance found in answer",
            )

        # Calculate difference
        if ground_truth == 0:
            pct_diff = 100 if agent_balance > 0 else 0
        else:
            pct_diff = abs(agent_balance - ground_truth) / ground_truth * 100

        # Check order of magnitude
        if ground_truth > 0:
            magnitude_match = (
                0.001 * ground_truth <= agent_balance <= 1000 * ground_truth
            )
        else:
            magnitude_match = agent_balance < 1

        if pct_diff <= 20:
            score = 1.0
        elif magnitude_match:
            score = 0.5
        else:
            score = 0.0

        return ValidationResult(
            score=score,
            is_correct=score >= 0.8,
            expected=f"τ{ground_truth:.6f}",
            actual=f"τ{agent_balance:.6f}" if agent_balance else answer,
            details=f"Difference: {pct_diff:.1f}%",
        )
