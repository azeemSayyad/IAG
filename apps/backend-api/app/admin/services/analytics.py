"""
Analytics Dashboard Service (Step 9.2)

Track KPIs:
- Booking rate
- Reply rate
- No-show rate
- Conversion rate
- Agent utilization
- Revenue per agent
- Cost per appointment
- Lead-to-booking time
"""

from datetime import datetime, date, timedelta, timezone
from typing import Dict, List, Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from app.core.config import settings
from app.models.lead import Lead
from app.models.appointment import Appointment
from app.models.agent import Agent
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.campaign import Campaign


def get_tenant_analytics(
    db: Session,
    tenant_id: str,
    start_date: date = None,
    end_date: date = None,
) -> Dict:
    """
    Get comprehensive analytics for a tenant.
    """
    # "Today" (and every date here) is the Eastern BUSINESS day, not UTC: the
    # call center runs on America/New_York, so KPIs like "Sent text today" must
    # reset at ET midnight — not at 8pm ET (UTC midnight). We anchor the date
    # window to ET midnight and convert to UTC for the timestamp comparison.
    # ZoneInfo handles DST automatically (EDT/EST).
    biz_tz = ZoneInfo(settings.AGENT_TZ)
    if not start_date:
        start_date = datetime.now(biz_tz).date() - timedelta(days=30)
    if not end_date:
        end_date = datetime.now(biz_tz).date()

    start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=biz_tz).astimezone(timezone.utc)
    end_dt = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=biz_tz).astimezone(timezone.utc)

    # Lead metrics
    total_leads = (
        db.query(Lead)
        .filter(
            Lead.tenant_id == tenant_id,
            Lead.created_at >= start_dt,
            Lead.created_at < end_dt,
        )
        .count()
    )

    contacted_leads = (
        db.query(Lead)
        .filter(
            Lead.tenant_id == tenant_id,
            Lead.status.in_(["contacted", "replied", "qualified", "booked", "completed"]),
            Lead.created_at >= start_dt,
            Lead.created_at < end_dt,
        )
        .count()
    )

    replied_leads = (
        db.query(Lead)
        .filter(
            Lead.tenant_id == tenant_id,
            Lead.status.in_(["replied", "qualified", "booked", "completed"]),
            Lead.created_at >= start_dt,
            Lead.created_at < end_dt,
        )
        .count()
    )

    booked_leads = (
        db.query(Lead)
        .filter(
            Lead.tenant_id == tenant_id,
            Lead.status.in_(["booked", "completed"]),
            Lead.created_at >= start_dt,
            Lead.created_at < end_dt,
        )
        .count()
    )

    # Appointment metrics
    total_appointments = (
        db.query(Appointment)
        .filter(
            Appointment.tenant_id == tenant_id,
            Appointment.created_at >= start_dt,
            Appointment.created_at < end_dt,
        )
        .count()
    )

    completed_appointments = (
        db.query(Appointment)
        .filter(
            Appointment.tenant_id == tenant_id,
            Appointment.status == "completed",
            Appointment.created_at >= start_dt,
            Appointment.created_at < end_dt,
        )
        .count()
    )

    no_show_appointments = (
        db.query(Appointment)
        .filter(
            Appointment.tenant_id == tenant_id,
            Appointment.status == "no_show",
            Appointment.created_at >= start_dt,
            Appointment.created_at < end_dt,
        )
        .count()
    )

    won_appointments = (
        db.query(Appointment)
        .filter(
            Appointment.tenant_id == tenant_id,
            Appointment.disposition == "won",
            Appointment.created_at >= start_dt,
            Appointment.created_at < end_dt,
        )
        .count()
    )

    # Agent metrics
    total_agents = (
        db.query(Agent)
        .filter(
            Agent.tenant_id == tenant_id,
            Agent.status == "active",
        )
        .count()
    )

    # Messages sent + positive replies in the period. The dashboard calls this
    # with start=end=today, so these become "texts sent today" and "customers
    # who replied yes/positive today".
    from app.models.message import Message
    from sqlalchemy import distinct as _distinct
    messages_sent = (
        db.query(Message)
        .filter(
            Message.tenant_id == tenant_id,
            Message.sender != "customer",            # outbound = ai / agent / system
            Message.created_at >= start_dt,
            Message.created_at < end_dt,
        )
        .count()
    )
    # "Total delivered" = outbound texts the provider CONFIRMED delivered to the
    # customer (DLR status == delivered). Subset of messages_sent; the rest are
    # still in flight (no DLR yet) or failed.
    delivered_texts = (
        db.query(Message)
        .filter(
            Message.tenant_id == tenant_id,
            Message.sender != "customer",
            func.lower(Message.delivery_status) == "delivered",
            Message.created_at >= start_dt,
            Message.created_at < end_dt,
        )
        .count()
    )
    positive_replies = (
        db.query(_distinct(Message.conversation_id))
        .filter(
            Message.tenant_id == tenant_id,
            Message.sender == "customer",
            Message.intent.in_(["POSITIVE", "BOOK_NOW", "INTERESTED", "SLOT_SELECTED"]),
            Message.created_at >= start_dt,
            Message.created_at < end_dt,
        )
        .count()
    )
    # "Failed text" = outbound texts the provider could NOT deliver to the
    # customer. delivery_status is the (lowercased) provider DLR status; a
    # terminal failure is failed / undelivered / rejected / expired. Messages
    # with no DLR yet (NULL) are not counted — only known failures.
    _FAILED_STATUSES = ["failed", "undelivered", "rejected", "expired"]
    failed_texts = (
        db.query(Message)
        .filter(
            Message.tenant_id == tenant_id,
            Message.sender != "customer",
            func.lower(Message.delivery_status).in_(_FAILED_STATUSES),
            Message.created_at >= start_dt,
            Message.created_at < end_dt,
        )
        .count()
    )
    # Campaign run/pause counts are CURRENT state (not period-scoped) and use the
    # same scope as the campaign-manager cards: CSV-upload campaigns, not deleted.
    _camp_base = db.query(Campaign).filter(
        Campaign.tenant_id == tenant_id,
        Campaign.deleted_at.is_(None),
        Campaign.description == "upload_batch",
    )
    campaigns_running = _camp_base.filter(Campaign.send_state == "running").count()
    campaigns_paused = _camp_base.filter(Campaign.send_state == "paused").count()

    # Calculate rates
    reply_rate = round(replied_leads / contacted_leads * 100, 1) if contacted_leads > 0 else 0
    booking_rate = round(booked_leads / contacted_leads * 100, 1) if contacted_leads > 0 else 0
    no_show_rate = round(no_show_appointments / total_appointments * 100, 1) if total_appointments > 0 else 0
    conversion_rate = round(won_appointments / completed_appointments * 100, 1) if completed_appointments > 0 else 0

    return {
        "period": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
        "leads": {
            "total": total_leads,
            "contacted": contacted_leads,
            "replied": replied_leads,
            "booked": booked_leads,
            "reply_rate": reply_rate,
            "booking_rate": booking_rate,
        },
        "appointments": {
            "total": total_appointments,
            "completed": completed_appointments,
            "no_show": no_show_appointments,
            "won": won_appointments,
            "no_show_rate": no_show_rate,
            "conversion_rate": conversion_rate,
        },
        "agents": {
            "total_active": total_agents,
        },
        "messages_sent": messages_sent,        # "Sent text" (outbound texts in period)
        "delivered_texts": delivered_texts,    # "Total delivered" (DLR-confirmed delivered)
        "positive_replies": positive_replies,  # "Total yes" (customers who replied positive)
        "failed_texts": failed_texts,          # "Failed text" (sent but not delivered)
        "campaigns_running": campaigns_running,  # campaigns currently sending
        "campaigns_paused": campaigns_paused,    # campaigns currently paused
    }


def get_agent_analytics(
    db: Session,
    tenant_id: str,
    agent_id: UUID = None,
    start_date: date = None,
    end_date: date = None,
) -> List[Dict]:
    """
    Get analytics per agent.
    """
    if not start_date:
        start_date = date.today() - timedelta(days=30)
    if not end_date:
        end_date = date.today()

    start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
    end_dt = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)

    query = db.query(Agent).filter(
        Agent.tenant_id == tenant_id,
        Agent.status == "active",
    )

    if agent_id:
        query = query.filter(Agent.id == agent_id)

    agents = query.all()

    agent_stats = []
    for agent in agents:
        # Get appointments
        appointments = (
            db.query(Appointment)
            .filter(
                Appointment.agent_id == agent.id,
                Appointment.created_at >= start_dt,
                Appointment.created_at < end_dt,
            )
            .all()
        )

        total = len(appointments)
        completed = sum(1 for a in appointments if a.status == "completed")
        no_show = sum(1 for a in appointments if a.status == "no_show")
        won = sum(1 for a in appointments if a.disposition == "won")

        # Calculate utilization
        total_minutes = sum(
            (a.end_time - a.start_time).total_seconds() / 60
            for a in appointments
            if a.status in ("confirmed", "completed")
        )
        available_minutes = 660 * (end_date - start_date).days  # 11 hours per day
        utilization = round(total_minutes / available_minutes * 100, 1) if available_minutes > 0 else 0

        # Get user info
        user = agent.user

        agent_stats.append({
            "agent_id": str(agent.id),
            "name": f"{user.first_name} {user.last_name}" if user else "Unknown",
            "metrics": {
                "total_appointments": total,
                "completed": completed,
                "no_show": no_show,
                "won": won,
                "utilization_pct": utilization,
                "win_rate": round(won / completed * 100, 1) if completed > 0 else 0,
            },
        })

    return agent_stats


def get_campaign_analytics(
    db: Session,
    tenant_id: str,
    start_date: date = None,
    end_date: date = None,
) -> List[Dict]:
    """
    Get analytics per campaign.
    """
    campaigns = (
        db.query(Campaign)
        .filter(
            Campaign.tenant_id == tenant_id,
            Campaign.deleted_at.is_(None),
        )
        .all()
    )

    campaign_stats = []
    for campaign in campaigns:
        stats = {
            "campaign_id": str(campaign.id),
            "name": campaign.name,
            "status": campaign.status,
            "metrics": {
                "total_leads": campaign.total_leads or 0,
                "contacted": campaign.total_contacted or 0,
                "replied": campaign.total_replied or 0,
                "booked": campaign.total_booked or 0,
                "completed": campaign.total_completed or 0,
                "won": campaign.total_won or 0,
                "reply_rate": round((campaign.total_replied or 0) / (campaign.total_contacted or 1) * 100, 1),
                "booking_rate": round((campaign.total_booked or 0) / (campaign.total_contacted or 1) * 100, 1),
                "conversion_rate": round((campaign.total_won or 0) / (campaign.total_completed or 1) * 100, 1),
            },
        }
        campaign_stats.append(stats)

    return campaign_stats


def get_daily_trends(
    db: Session,
    tenant_id: str,
    days: int = 30,
) -> List[Dict]:
    """
    Get daily trends for the last N days.
    """
    trends = []
    today = date.today()

    for i in range(days):
        target_date = today - timedelta(days=days - 1 - i)
        start_dt = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
        end_dt = datetime.combine(target_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)

        new_leads = (
            db.query(Lead)
            .filter(
                Lead.tenant_id == tenant_id,
                Lead.created_at >= start_dt,
                Lead.created_at < end_dt,
            )
            .count()
        )

        new_appointments = (
            db.query(Appointment)
            .filter(
                Appointment.tenant_id == tenant_id,
                Appointment.created_at >= start_dt,
                Appointment.created_at < end_dt,
            )
            .count()
        )

        completed = (
            db.query(Appointment)
            .filter(
                Appointment.tenant_id == tenant_id,
                Appointment.status == "completed",
                Appointment.start_time >= start_dt,
                Appointment.start_time < end_dt,
            )
            .count()
        )

        won = (
            db.query(Appointment)
            .filter(
                Appointment.tenant_id == tenant_id,
                Appointment.disposition == "won",
                Appointment.start_time >= start_dt,
                Appointment.start_time < end_dt,
            )
            .count()
        )

        trends.append({
            "date": target_date.isoformat(),
            "new_leads": new_leads,
            "new_appointments": new_appointments,
            "completed": completed,
            "won": won,
        })

    return trends
