"""
AI Knowledge Base (Phase 37.6)

Stores and retrieves insurance domain knowledge:
- Insurance product knowledge
- Pricing policies and guidelines
- Compliance rules (TCPA, state regulations)
- Common rebuttals and responses
- Frequently asked questions

Usage:
    kb = KnowledgeBase(db, tenant_id)
    await kb.seed_default_knowledge()
    results = await kb.search("auto insurance coverage")
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.analytics.vector_search import qdrant_client
from app.ai.conversation_engine.retrieval import EmbeddingGenerator

logger = logging.getLogger(__name__)


# --- Default Knowledge Entries ---

INSURANCE_KNOWLEDGE = [
    {
        "category": "auto_insurance",
        "topic": "coverage_types",
        "text": "Auto insurance typically includes liability coverage (bodily injury and property damage), collision coverage (damage to your vehicle), comprehensive coverage (theft, weather, vandalism), uninsured/underinsured motorist coverage, and personal injury protection (PIP). Each state has minimum liability requirements.",
    },
    {
        "category": "auto_insurance",
        "topic": "factors_affecting_rates",
        "text": "Auto insurance rates are affected by: driving record (accidents, tickets), age and gender, vehicle type and value, annual mileage, credit score, location (urban vs rural), coverage levels chosen, and deductible amount. Safe drivers with good credit typically get the best rates.",
    },
    {
        "category": "auto_insurance",
        "topic": "discounts",
        "text": "Common auto insurance discounts include: multi-policy bundle (home + auto), safe driver discount, good student discount, military/veteran discount, anti-theft device discount, low mileage discount, paid-in-full discount, and loyalty discount. Ask your agent which discounts you qualify for.",
    },
    {
        "category": "homeowners_insurance",
        "topic": "coverage_basics",
        "text": "Homeowners insurance covers: dwelling (structure of your home), personal property (belongings inside), liability (injuries on your property), and additional living expenses (if home is uninhabitable). Flood and earthquake typically require separate policies.",
    },
    {
        "category": "homeowners_insurance",
        "topic": "what_is_not_covered",
        "text": "Standard homeowners insurance typically does NOT cover: flood damage (requires separate flood insurance), earthquake damage, normal wear and tear, pest infestations, intentional damage, and business equipment. Riders can be added for high-value items like jewelry or art.",
    },
    {
        "category": "life_insurance",
        "topic": "types",
        "text": "Life insurance comes in two main types: Term life (coverage for a specific period, 10-30 years, lower cost) and Permanent life (whole life, universal life - coverage for life with cash value component). Term is best for temporary needs; permanent is better for estate planning and lifelong coverage.",
    },
    {
        "category": "life_insurance",
        "topic": "how_much_needed",
        "text": "A common rule of thumb is 10-15 times your annual income in life insurance coverage. Consider: outstanding debts (mortgage, loans), children's education costs, funeral expenses, and ongoing family living expenses. An agent can help calculate your specific needs.",
    },
    {
        "category": "health_insurance",
        "topic": "plan_types",
        "text": "Health insurance plan types include: HMO (Health Maintenance Organization - requires primary care physician referrals), PPO (Preferred Provider Organization - more flexibility, higher premiums), EPO (Exclusive Provider Organization - no out-of-network coverage), and HDHP (High Deductible Health Plan - lower premiums, higher deductibles, HSA eligible).",
    },
    {
        "category": "general",
        "topic": "why_insurance_matters",
        "text": "Insurance protects you from financial devastation. Without insurance, a single accident, illness, or disaster could cost hundreds of thousands of dollars. Insurance transfers that risk to the insurance company in exchange for affordable monthly premiums. It's not just a requirement - it's peace of mind for you and your family.",
    },
    {
        "category": "general",
        "topic": "how_to_save_money",
        "text": "Ways to save on insurance: bundle multiple policies (home + auto), raise your deductible (higher deductible = lower premium), maintain good credit, ask about all available discounts, shop around annually, maintain a clean driving record, install safety/security devices, and review your coverage needs regularly.",
    },
]

COMPLIANCE_RULES = [
    {
        "category": "tcpa",
        "topic": "opt_out_requirements",
        "text": "TCPA requires honoring opt-out requests immediately. If a consumer says 'stop', 'unsubscribe', 'remove me', or similar, you must cease all contact within 24 hours. Document the opt-out request and add the number to your suppression list. Failure to comply can result in fines of $500-$1,500 per violation.",
    },
    {
        "category": "tcpa",
        "topic": "calling_hours",
        "text": "TCPA restricts telemarketing calls to between 8:00 AM and 9:00 PM in the consumer's local time zone. This applies to both calls and text messages. Always check the consumer's timezone before contacting. Weekend calls follow the same hours.",
    },
    {
        "category": "tcpa",
        "topic": "consent_requirements",
        "text": "Before sending marketing messages, you need prior express written consent. This consent must be clearly disclosed and include: the purpose of the communication, the company name, and acknowledgment that consent is not a condition of purchase. Keep records of all consent.",
    },
    {
        "category": "compliance",
        "topic": "data_privacy",
        "text": "Protect customer data: never share personal information (SSN, date of birth, financial details) via SMS. Store all customer data securely. Follow state-specific privacy laws (CCPA in California, etc.). Only collect information necessary for the insurance quote.",
    },
    {
        "category": "compliance",
        "topic": "prohibited_claims",
        "text": "Never make: guaranteed coverage promises (underwriting is required), false pricing claims (rates vary by individual), medical or legal advice (you are not a licensed professional), misleading comparisons to competitors, or claims about government programs unless authorized.",
    },
]

COMMON_REBUTTALS = [
    {
        "category": "pricing_objection",
        "topic": "too_expensive",
        "text": "When a customer says it's too expensive: Acknowledge the concern, explain the value of coverage, mention available discounts, offer to adjust coverage levels or deductibles, and compare the cost to the potential financial risk of being uninsured. Example: 'I understand cost is important. Let me see what discounts you qualify for - many customers save 15-25% with our bundle options.'",
    },
    {
        "category": "pricing_objection",
        "topic": "competitor_cheaper",
        "text": "When a customer mentions a cheaper competitor: Don't badmouth the competitor. Instead, focus on your value proposition - coverage quality, claims process, customer service, financial stability. Offer to do a side-by-side comparison. Example: 'That's a great rate! I'd love to show you what's included in our coverage - sometimes the cheapest option doesn't cover everything you need.'",
    },
    {
        "category": "trust_objection",
        "topic": "never_heard_of_you",
        "text": "When a customer doesn't know your company: Share your company's history, financial ratings (A.M. Best, S&P), number of customers served, claims payment record, and any awards or recognitions. Offer to send informational materials. Example: 'We've been serving customers for over 20 years with an A+ rating from A.M. Best. We've paid out over $X billion in claims.'",
    },
    {
        "category": "timing_objection",
        "topic": "not_right_now",
        "text": "When a customer says it's not the right time: Respect their timing but emphasize that insurance needs don't wait. Offer to schedule a follow-up at their convenience. Leave the door open. Example: 'I completely understand. Insurance needs can change quickly though - would it be helpful if I checked back in a month or two?'",
    },
    {
        "category": "trust_objection",
        "topic": "already_have_insurance",
        "text": "When a customer already has insurance: Don't try to replace it immediately. Offer a free review/comparison to ensure they have the best coverage for their needs. Many people are overpaying or underinsured. Example: 'That's great that you're covered! Would you like a free review to make sure you're getting the best value? Many customers find they can save money or get better coverage.'",
    },
]

FAQ_ENTRIES = [
    {
        "category": "faq",
        "topic": "how_to_get_quote",
        "text": "Getting a quote is easy! Just provide some basic information: your name, address, vehicle/home details (depending on insurance type), and coverage preferences. Most quotes take less than 5 minutes and you can get them over the phone, online, or through our app. There's no obligation and no credit check for a quote.",
    },
    {
        "category": "faq",
        "topic": "how_to_file_claim",
        "text": "To file a claim: call our 24/7 claims line or file online through your account. Have your policy number, date/time of incident, description of what happened, and any photos or documentation. A claims adjuster will be assigned within 24 hours. Most claims are processed within 7-14 business days.",
    },
    {
        "category": "faq",
        "topic": "how_to_cancel",
        "text": "To cancel your policy: contact your agent or call customer service. You may be eligible for a prorated refund of unused premiums. There may be a short-rate cancellation fee depending on your policy terms. We recommend having new coverage in place before canceling to avoid a coverage gap.",
    },
    {
        "category": "faq",
        "topic": "what_affects_premium",
        "text": "Your premium is affected by: coverage types and limits selected, deductible amount, your claims history, credit score (in most states), age, location, vehicle/home details, and available discounts. Adjusting your deductible or coverage levels can significantly impact your premium.",
    },
    {
        "category": "faq",
        "topic": "when_does_coverage_start",
        "text": "Coverage typically starts immediately upon payment and policy issuance. For auto insurance, you can often get same-day coverage. Homeowners insurance may require an inspection first. You'll receive your policy documents and proof of insurance within 24-48 hours of purchase.",
    },
]


class KnowledgeBase:
    """
    Manages the AI knowledge base for insurance domain.

    Provides:
    - Default knowledge seeding
    - Custom knowledge addition
    - Semantic search
    - Knowledge management
    """

    def __init__(self, db: Session, tenant_id: str):
        self.db = db
        self.tenant_id = tenant_id
        self.embedding_gen = EmbeddingGenerator()

    async def seed_default_knowledge(self) -> Dict[str, int]:
        """
        Seed the knowledge base with default insurance knowledge.

        Returns:
            Dict with counts per category
        """
        results = {
            "insurance": 0,
            "compliance": 0,
            "rebuttals": 0,
            "faq": 0,
        }

        # Seed insurance knowledge
        for entry in INSURANCE_KNOWLEDGE:
            success = await self._store_entry(entry)
            if success:
                results["insurance"] += 1

        # Seed compliance rules
        for entry in COMPLIANCE_RULES:
            success = await self._store_entry(entry)
            if success:
                results["compliance"] += 1

        # Seed rebuttals
        for entry in COMMON_REBUTTALS:
            success = await self._store_entry(entry)
            if success:
                results["rebuttals"] += 1

        # Seed FAQ
        for entry in FAQ_ENTRIES:
            success = await self._store_entry(entry)
            if success:
                results["faq"] += 1

        total = sum(results.values())
        logger.info(f"Seeded {total} knowledge entries for tenant {self.tenant_id}")

        return results

    async def add_knowledge(
        self,
        category: str,
        topic: str,
        text: str,
        source: str = "manual",
    ) -> bool:
        """
        Add a custom knowledge entry.

        Args:
            category: Knowledge category
            topic: Topic within category
            text: Knowledge text
            source: Source of knowledge

        Returns:
            True if stored successfully
        """
        entry = {
            "category": category,
            "topic": topic,
            "text": text,
            "source": source,
        }

        return await self._store_entry(entry)

    async def search(
        self,
        query: str,
        category: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict]:
        """
        Search the knowledge base.

        Args:
            query: Search query
            category: Optional category filter
            limit: Maximum results

        Returns:
            List of matching knowledge entries
        """
        from app.analytics.vector_search import build_tenant_filter, build_tenant_type_filter

        embedding = await self.embedding_gen.generate(query)

        if category:
            filter_conditions = build_tenant_type_filter(
                self.tenant_id, category, "category"
            )
        else:
            filter_conditions = build_tenant_filter(self.tenant_id)

        results = await qdrant_client.search(
            collection_name="knowledge_base",
            query_vector=embedding,
            limit=limit,
            score_threshold=0.4,
            filter_conditions=filter_conditions,
        )

        return [
            {
                "text": r.get("payload", {}).get("text", ""),
                "category": r.get("payload", {}).get("category", ""),
                "topic": r.get("payload", {}).get("topic", ""),
                "score": r.get("score", 0.0),
            }
            for r in results
        ]

    async def get_by_category(self, category: str) -> List[Dict]:
        """Get all knowledge entries for a category."""
        from app.analytics.vector_search import build_tenant_type_filter

        filter_conditions = build_tenant_type_filter(
            self.tenant_id, category, "category"
        )

        # Use a generic query to find all entries in category
        embedding = await self.embedding_gen.generate(category)

        results = await qdrant_client.search(
            collection_name="knowledge_base",
            query_vector=embedding,
            limit=50,
            score_threshold=0.0,
            filter_conditions=filter_conditions,
        )

        return [
            {
                "text": r.get("payload", {}).get("text", ""),
                "category": r.get("payload", {}).get("category", ""),
                "topic": r.get("payload", {}).get("topic", ""),
            }
            for r in results
        ]

    async def count(self) -> int:
        """Count total knowledge entries for this tenant."""
        from app.analytics.vector_search import build_tenant_filter

        return await qdrant_client.count(
            collection_name="knowledge_base",
            filter_conditions=build_tenant_filter(self.tenant_id),
        )

    async def _store_entry(self, entry: Dict) -> bool:
        """Store a knowledge entry in the vector store."""
        text = entry["text"]
        category = entry["category"]
        topic = entry.get("topic", "general")
        source = entry.get("source", "default")

        # Generate embedding
        embedding = await self.embedding_gen.generate(text)

        # Create unique ID
        content_hash = hashlib.md5(text.encode()).hexdigest()[:12]
        doc_id = f"kb_{category}_{topic}_{content_hash}"

        # Store
        payload = {
            "tenant_id": self.tenant_id,
            "category": category,
            "topic": topic,
            "source": source,
            "text": text[:1000],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        success = await qdrant_client.upsert_vector(
            collection_name="knowledge_base",
            vector_id=doc_id,
            vector=embedding,
            payload=payload,
        )

        if success:
            logger.debug(f"Stored knowledge: {category}/{topic}")

        return success
