"""Transfer query template for Taostats"""

import os
import random
from typing import Any, Dict, List, Optional
import aiohttp

from liveweb_arena.core.validators.base import (
    QuestionTemplate, GeneratedQuestion, ValidationResult, register_template,
)


# Known addresses with transfer activity
KNOWN_ADDRESSES = [
    ("5GcCZ2BPXBjgG88tXJCEtkbdg2hNrPbL4EFfbiVRvBZdSQDC", "Taostats validator"),
    ("5HCFWvRqzSHWRPecN7q8J6c7aKQnrCZTMHstPv39xL1wgDHh", "Apex subnet owner"),
]


@register_template("taostats_transfer")
class TransferTemplate(QuestionTemplate):
    """
    Template for transfer queries.

    Tests AI's ability to search for account transfer history.
    Ground truth from Taostats API (requires TAOSTATS_API_KEY).
    """

    TAOSTATS_API = "https://api.taostats.io/api/transfer/v1"

    PATTERNS: List[str] = [
        "Search for address {address} on taostats.io and find how many transfers it has made.",
        "Go to taostats.io, look up {address}, and count its total transfers.",
        "How many transfers has wallet {address} made? Check on taostats.io.",
    ]

    def __init__(self):
        super().__init__("taostats_transfer")
        self.api_key = os.getenv("TAOSTATS_API_KEY")

    def generate(self, seed: int) -> GeneratedQuestion:
        rng = random.Random(seed)

        address, description = rng.choice(KNOWN_ADDRESSES)
        pattern = rng.choice(self.PATTERNS)

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
        return f"""Task-Specific Rules (Transfer Count: {short_addr}):
- Score 1.0: Agent provides transfer count within 10% of actual
- Score 0.5: Agent provides count within 50% of actual
- Score 0.0: No count, wrong format, or wildly incorrect"""

    async def get_ground_truth(self, validation_info: Dict[str, Any]) -> Optional[int]:
        """Fetch transfer count from Taostats API"""
        if not self.api_key:
            return None

        address = validation_info.get("address", "")
        if not address:
            return None

        try:
            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": self.api_key}
                # Query transfers from this address
                params = {"from": address, "limit": 1}

                async with session.get(
                    self.TAOSTATS_API,
                    headers=headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status != 200:
                        return None
                    data = await response.json()

                    # Get total from pagination
                    pagination = data.get("pagination", {})
                    total_sent = pagination.get("total_items", 0)

                # Also query transfers to this address
                params = {"to": address, "limit": 1}
                async with session.get(
                    self.TAOSTATS_API,
                    headers=headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status != 200:
                        return total_sent
                    data = await response.json()

                    pagination = data.get("pagination", {})
                    total_received = pagination.get("total_items", 0)

                # Return total transfers (sent + received)
                return total_sent + total_received

        except Exception:
            return None

    async def validate_answer(
        self, answer: str, validation_info: Dict[str, Any]
    ) -> ValidationResult:
        """Validate transfer count answer"""
        import re

        ground_truth = await self.get_ground_truth(validation_info)

        if ground_truth is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=None,
                actual=answer,
                details="Ground truth unavailable (need TAOSTATS_API_KEY)",
            )

        # Extract number from answer
        numbers = re.findall(r'[\d,]+', answer.replace(',', ''))

        agent_count = None
        for n in numbers:
            try:
                count = int(n.replace(',', ''))
                if count >= 0:
                    agent_count = count
                    break
            except ValueError:
                continue

        if agent_count is None:
            return ValidationResult(
                score=0.0,
                is_correct=False,
                expected=str(ground_truth),
                actual=answer,
                details="No valid count found in answer",
            )

        # Calculate percentage difference
        if ground_truth == 0:
            pct_diff = 100 if agent_count > 0 else 0
        else:
            pct_diff = abs(agent_count - ground_truth) / ground_truth * 100

        if pct_diff <= 10:
            score = 1.0
        elif pct_diff <= 50:
            score = 0.5
        else:
            score = 0.0

        return ValidationResult(
            score=score,
            is_correct=score >= 0.8,
            expected=str(ground_truth),
            actual=str(agent_count),
            details=f"Difference: {pct_diff:.1f}%",
        )
