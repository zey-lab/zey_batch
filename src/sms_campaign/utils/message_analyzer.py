"""SMS message cost analysis and validation."""

import re
from typing import Dict, List, Tuple


class MessageAnalyzer:
    """Analyze SMS messages for cost and encoding."""

    # GSM-7 character set (basic ASCII that doesn't trigger Unicode)
    GSM_7_BASIC = set(
        "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
        "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
    )

    # GSM-7 extended characters (count as 2 characters)
    GSM_7_EXTENDED = set("^{}\\[~]|€")

    # Common emojis to detect
    EMOJI_PATTERN = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F1E0-\U0001F1FF"  # flags
        "\U00002702-\U000027B0"
        "\U000024C2-\U0001F251"
        "\U0001F900-\U0001F9FF"  # supplemental symbols
        "\U0001F018-\U0001F270"
        "]+",
        flags=re.UNICODE
    )

    # Characters that trigger Unicode encoding
    UNICODE_TRIGGERS = set("\u2018\u2019\u201c\u201d\u2014\u2013\u00a9\u00ae\u2122\u00b1\u00d7\u00f7")

    def __init__(self):
        """Initialize message analyzer."""
        # SMS segment limits
        self.GSM_SINGLE_LIMIT = 160
        self.GSM_MULTI_SEGMENT = 153
        self.UNICODE_SINGLE_LIMIT = 70
        self.UNICODE_MULTI_SEGMENT = 67

        # Cost per segment (approximate US pricing)
        self.COST_PER_SEGMENT = 0.0079  # Twilio US pricing

    def analyze_message(self, message: str) -> Dict:
        """
        Analyze a message for encoding, length, segments, and cost.

        Args:
            message: The SMS message to analyze

        Returns:
            Dictionary with analysis results
        """
        if not message:
            return {
                'length': 0,
                'encoding': 'GSM-7',
                'segments': 0,
                'cost': 0.0,
                'is_optimal': True,
                'warnings': [],
                'recommendations': []
            }

        # Detect encoding
        encoding, unicode_chars = self._detect_encoding(message)

        # Calculate effective length (considering extended chars)
        effective_length = self._calculate_effective_length(message, encoding)

        # Calculate segments
        segments = self._calculate_segments(effective_length, encoding)

        # Calculate cost
        cost = segments * self.COST_PER_SEGMENT

        # Determine if optimal
        is_optimal = (
            encoding == 'GSM-7' and
            effective_length <= self.GSM_SINGLE_LIMIT
        )

        # Generate warnings and recommendations
        warnings = self._generate_warnings(
            message, encoding, effective_length, segments, unicode_chars
        )
        recommendations = self._generate_recommendations(
            message, encoding, effective_length, segments, unicode_chars
        )

        return {
            'length': len(message),
            'effective_length': effective_length,
            'encoding': encoding,
            'segments': segments,
            'cost': cost,
            'cost_formatted': f"${cost:.4f}",
            'is_optimal': is_optimal,
            'warnings': warnings,
            'recommendations': recommendations,
            'unicode_characters': list(unicode_chars) if unicode_chars else [],
            'chars_remaining': self._chars_remaining(effective_length, encoding),
        }

    def _detect_encoding(self, message: str) -> Tuple[str, set]:
        """
        Detect if message uses GSM-7 or Unicode encoding.

        Returns:
            Tuple of (encoding_name, set_of_unicode_chars)
        """
        unicode_chars = set()

        # Check for emojis
        if self.EMOJI_PATTERN.search(message):
            emojis = self.EMOJI_PATTERN.findall(message)
            unicode_chars.update(emojis)
            return 'Unicode (UCS-2)', unicode_chars

        # Check each character
        for char in message:
            # Check if it's outside GSM-7 charset
            if char not in self.GSM_7_BASIC and char not in self.GSM_7_EXTENDED:
                # Check if it's a known Unicode trigger
                if char in self.UNICODE_TRIGGERS or ord(char) > 127:
                    unicode_chars.add(char)

        if unicode_chars:
            return 'Unicode (UCS-2)', unicode_chars

        return 'GSM-7', set()

    def _calculate_effective_length(self, message: str, encoding: str) -> int:
        """
        Calculate effective character count.
        GSM-7 extended chars count as 2 characters.
        """
        if encoding != 'GSM-7':
            return len(message)

        # Count extended characters (they use 2 chars each)
        extended_count = sum(1 for char in message if char in self.GSM_7_EXTENDED)

        return len(message) + extended_count

    def _calculate_segments(self, effective_length: int, encoding: str) -> int:
        """Calculate number of SMS segments needed."""
        if effective_length == 0:
            return 0

        if encoding == 'GSM-7':
            if effective_length <= self.GSM_SINGLE_LIMIT:
                return 1
            else:
                # Multi-part messages use 153 chars per segment
                return (effective_length - 1) // self.GSM_MULTI_SEGMENT + 1
        else:
            # Unicode encoding
            if effective_length <= self.UNICODE_SINGLE_LIMIT:
                return 1
            else:
                # Multi-part Unicode uses 67 chars per segment
                return (effective_length - 1) // self.UNICODE_MULTI_SEGMENT + 1

    def _chars_remaining(self, effective_length: int, encoding: str) -> int:
        """Calculate characters remaining in current segment."""
        if encoding == 'GSM-7':
            if effective_length <= self.GSM_SINGLE_LIMIT:
                return self.GSM_SINGLE_LIMIT - effective_length
            else:
                # Find position in current segment
                in_segment = effective_length % self.GSM_MULTI_SEGMENT
                if in_segment == 0:
                    in_segment = self.GSM_MULTI_SEGMENT
                return self.GSM_MULTI_SEGMENT - in_segment
        else:
            if effective_length <= self.UNICODE_SINGLE_LIMIT:
                return self.UNICODE_SINGLE_LIMIT - effective_length
            else:
                in_segment = effective_length % self.UNICODE_MULTI_SEGMENT
                if in_segment == 0:
                    in_segment = self.UNICODE_MULTI_SEGMENT
                return self.UNICODE_MULTI_SEGMENT - in_segment

    def _generate_warnings(
        self,
        message: str,
        encoding: str,
        effective_length: int,
        segments: int,
        unicode_chars: set
    ) -> List[str]:
        """Generate warnings about message cost."""
        warnings = []

        # Emoji warning
        if self.EMOJI_PATTERN.search(message):
            warnings.append(
                "⚠️ EMOJIS DETECTED: Message uses expensive Unicode encoding"
            )

        # Unicode character warning
        if encoding != 'GSM-7' and unicode_chars and not self.EMOJI_PATTERN.search(message):
            chars_display = ', '.join(f"'{c}'" for c in list(unicode_chars)[:5])
            if len(unicode_chars) > 5:
                chars_display += f", ... (+{len(unicode_chars)-5} more)"
            warnings.append(
                f"⚠️ UNICODE CHARACTERS: {chars_display} trigger expensive encoding"
            )

        # Multi-segment warning
        if segments > 1:
            warnings.append(
                f"⚠️ MULTI-SEGMENT: Message will be sent as {segments} SMS segments"
            )

        # Cost warning
        if segments >= 2:
            warnings.append(
                f"💰 EXPENSIVE: Each message costs ${segments * self.COST_PER_SEGMENT:.4f} "
                f"({segments}x segments)"
            )

        # Very expensive warning
        if segments >= 3:
            warnings.append(
                f"🚨 VERY EXPENSIVE: {segments} segments = 3x+ normal cost!"
            )

        return warnings

    def _generate_recommendations(
        self,
        message: str,
        encoding: str,
        effective_length: int,
        segments: int,
        unicode_chars: set
    ) -> List[str]:
        """Generate recommendations to optimize message."""
        recommendations = []

        # Recommend removing emojis
        if self.EMOJI_PATTERN.search(message):
            recommendations.append(
                "✂️ Remove emojis to reduce cost by ~60% (Unicode→GSM-7)"
            )

        # Recommend replacing Unicode characters
        if encoding != 'GSM-7' and unicode_chars and not self.EMOJI_PATTERN.search(message):
            replacements = []
            if '"' in unicode_chars or '"' in unicode_chars:
                replacements.append('Use straight quotes " instead of curly quotes')
            if ''' in unicode_chars or ''' in unicode_chars:
                replacements.append("Use straight apostrophe ' instead of curly")
            if '—' in unicode_chars or '–' in unicode_chars:
                replacements.append('Use hyphen - instead of em/en dash')

            if replacements:
                recommendations.extend(
                    [f"✂️ {r}" for r in replacements]
                )

        # Recommend shortening if multi-segment
        if segments == 2:
            gsm_limit = self.GSM_SINGLE_LIMIT if encoding == 'GSM-7' else self.UNICODE_SINGLE_LIMIT
            chars_to_remove = effective_length - gsm_limit
            recommendations.append(
                f"✂️ Shorten by {chars_to_remove} characters to fit in 1 segment (save 50% cost)"
            )

        if segments >= 3:
            gsm_limit = self.GSM_SINGLE_LIMIT if encoding == 'GSM-7' else self.UNICODE_SINGLE_LIMIT
            chars_to_remove = effective_length - gsm_limit
            recommendations.append(
                f"✂️ HIGHLY RECOMMENDED: Shorten by {chars_to_remove} characters to reduce from "
                f"{segments} segments to 1 segment (save {((segments-1)/segments)*100:.0f}% cost)"
            )

        # If already optimal
        if encoding == 'GSM-7' and effective_length <= self.GSM_SINGLE_LIMIT:
            recommendations.append(
                "✅ OPTIMAL: Message uses cheapest encoding and fits in 1 segment"
            )

        return recommendations

    def analyze_campaign(self, messages: List[str]) -> Dict:
        """
        Analyze multiple messages (entire campaign).

        Args:
            messages: List of SMS messages

        Returns:
            Aggregated analysis
        """
        if not messages:
            return {
                'total_messages': 0,
                'total_segments': 0,
                'total_cost': 0.0,
                'avg_length': 0,
                'unicode_count': 0,
                'multi_segment_count': 0,
            }

        analyses = [self.analyze_message(msg) for msg in messages]

        total_segments = sum(a['segments'] for a in analyses)
        total_cost = sum(a['cost'] for a in analyses)
        unicode_count = sum(1 for a in analyses if a['encoding'] != 'GSM-7')
        multi_segment_count = sum(1 for a in analyses if a['segments'] > 1)
        avg_length = sum(a['length'] for a in analyses) / len(analyses)

        return {
            'total_messages': len(messages),
            'total_segments': total_segments,
            'total_cost': total_cost,
            'total_cost_formatted': f"${total_cost:.2f}",
            'avg_length': f"{avg_length:.1f}",
            'avg_segments': f"{total_segments / len(messages):.1f}",
            'unicode_count': unicode_count,
            'unicode_percentage': f"{(unicode_count / len(messages) * 100):.1f}%",
            'multi_segment_count': multi_segment_count,
            'multi_segment_percentage': f"{(multi_segment_count / len(messages) * 100):.1f}%",
            'optimal_count': sum(1 for a in analyses if a['is_optimal']),
        }
