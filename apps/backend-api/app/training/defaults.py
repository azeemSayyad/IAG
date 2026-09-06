"""The out-of-the-box training program.

Seeded once per tenant the first time the Training page is opened and the
tenant has no steps at all (not even soft-deleted ones — an admin who removes
every step is not re-seeded). Admins then edit freely.

`content` uses the line-based script markup the Training page renders:
    # Heading                    section heading
    [Agent]: "..."               speaker quote card (any [Label]: prefix)
    > note                       customer response / stage direction (italic)
    1. item                      numbered checklist
    - item                       bullet
    ++ Title | body              success (green) callout
    :: Title | body              neutral callout
    !! text                      critical (red) callout
    plain line                   paragraph
"""

SALES_SCRIPT = """# Phase 1 — Introduction, Reason for Call & Initial Consent
[Agent]: "Hello, my name is [Agent Name] with Insurance Alliance Group. This call is being recorded for quality and compliance purposes. We're reaching out in regards to the text message you responded to—there may have been a flag on your account, and we're making sure your health coverage is in good standing and helping you review your options through the marketplace. Do you know if your marketplace plan is still active?"
> Customer Response: Discusses plan status or confirms status.
[Agent]: "Got it. Do I have your permission to discuss your marketplace subsidy options today? This only takes a few minutes."
> Customer Response: Must answer clearly in the affirmative before moving forward.

# Phase 2 — Needs Discovery, Error Check & Household Assessment
[Agent]: "Fantastic. To investigate that potential flag, check for any errors on your file, make sure we find you the lowest possible monthly premium, and see if you qualify for cost-sharing reductions, I just have a few brief questions about your household:"
- "I have your zip code here at (ZIP). Is that correct?" [Customer Response]
- "Are you going to be claiming any children or a spouse on your taxes next year?" [Customer Response]
- "Perfect, so the subsidy you receive is based on your income for this year. What do you think you'll be making this year?" [Customer Response / If unsure:]
- "No problem! The state minimum for next year is $16,000, which is about $8 per hour. Do you think you'll be able to make that? If it ends up being less, that's fine, but if it's more, just let us know as soon as possible." [Customer Response]
> Agent Action: Input data securely into enrollment platform while checking for any account discrepancies, data mismatches, or eligibility flags.

# Phase 3 — Account Verification & Plan Selection
[Agent]: "Before we pull up your specific file and review available plans, do I have your permission to view your marketplace account?"
> Customer Response: Affirmative consent given to access existing marketplace data.
[Agent]: "Now, let me check if we have an account set up for you already. Can you please provide me with your first and last name and date of birth please?"
> Customer Response
[Agent]: "And what state do you reside in?"
> Customer Response
[Agent]: "Perfect! I found your account. Let's go over the details to make sure everything is correct to verify for accuracy."
> Verify: Address, Apartment number, Email, Phone number
[Agent]: "Great! Based on that, you qualify for a federal tax credit subsidy that significantly lowers your monthly cost. Your coverage begins on ____________ [Start Date Left Blank]. All of your preventative care will be free, your primary doctor will be ( ) and generic medications will be ( ). Your deductible will be ( ) and most importantly it's a free plan so no monthly cost whatsoever."
[Agent]: "By the way, what kind of work do you do, or what is your usual line of work?"
> Customer Response
[Agent]: "Got it, thank you! We have your income here as [Amount]. I'll be your agent moving forward. If anything changes, please let me know."
[Agent]: "You'll be receiving your insurance cards in about two weeks, and we'll send you some confirmation emails today as well."

# Phase 4 — CMS Verification Questions & Consent to Act
[Agent]: "Now, we just need to do a quick verification. I'll ask you a couple of questions to confirm the information we've already gone over. After that, I'll update your application and make sure I'm officially your agent for the next 365 days, so you'll only have to speak to me for anything related to your insurance."
[Proceed with verification questions]: "Before we complete your enrollment, I need to do a quick recording with you. Please listen carefully and respond with your confirmation to each question. This is a CMS requirement, and it's important to confirm your intent to enroll in the plan. Here we go:"
1. "Please state your zip code for the policy."
2. "Please confirm your date of birth."
3. "Please state the names of all individuals applying for coverage today."
4. "Can you verify your household income as [$Amount], and confirm that if your income changes you will notify us or the marketplace?"
5. "Your major medical plan is with [Carrier Name], and it is the [Plan Name] plan. Your monthly premium after subsidies is [$Amount], and the coverage begins on [Effective Date - Blank]. Do you acknowledge that this plan's benefits and any billing are separate from other products like dental or vision that you may choose to enroll in?"
6. "Do you authorize us to submit your major medical application to the marketplace today?"
7. "Do you agree that Insurance Alliance Group will act on your behalf as your Agent of Record, and that you can opt out of this consent at any time by calling 855-714-2861."
8. "Do you agree to receive important plan updates via text and email, and you can opt out of this consent at any time by replying STOP to any messages you receive?"
9. "Do we have your permission to assist you with submitting documents to the marketplace, including your income verification using the details you've provided today?"
10. "Do you acknowledge that you will be automatically re-enrolled in this plan in the future, and give us permission to enroll you in a similar plan if your current plan is no longer available at the same cost, to prevent any lapse in coverage?"

# Phase 5 — Agent Link Walkthrough & Marketplace Transfer
[Agent]: "Walk client thru agent link, if fails then do marketplace call."
++ Step A — Primary Method: Agent Link Walkthrough | "I'm going to send you a quick text/email with a secure link to connect your account with me as your official agent. Please open it up, click confirm, and let me know once you see the success confirmation screen." If client successfully completes the link: skip to final system submission and wrap-up. If the link fails or the client cannot complete it, proceed to Step B below.
:: Step B — Fallback Method: Marketplace Call | "No problem at all! We're about to call the marketplace together, and they'll verify that you're on the line with me. They'll ask for your name, date of birth, and address—so please make sure to provide the full address. Once they make me your agent for the next year, you can hang up, and I'll finish the process from my end!"
[Agent]: "Hi, my name is (AGENT), and I have my client (CLIENT'S NAME) on the line with us. We just need to make me their agent for the next 365 days."
> Marketplace Agent Verifies Client Information
[Agent]: "Thank you! Now that I'm your agent, you can hang up, and I'll finish the rest."
> Once Marketplace Confirms / Client Hangs Up
[Agent]: "I've updated all the information for their 2026 application. I just need you to push the enrollment through for me."
> Marketplace Agent Confirms
!! Final Action: Agree to disclosures and refresh to make sure it is under the correct NPN OVERRIDE!

# Phase 6 — Referral Request & Wrap-Up
[Agent]: "Before I let you go, do you have any friends, family members, or co-workers who might also need help reviewing their health coverage or finding a zero-dollar plan? We'd love to help take care of them just like we did for you!"
> Customer Response: Provides referral names/numbers or acknowledges.
[Agent]: "Thank you so much! Again, my name is [Agent Name] with Insurance Alliance Group, and my direct number is listed in the confirmation emails you'll receive today. Have a wonderful rest of your day!"
"""

# (title, description, video_url, content)
DEFAULT_STEPS = [
    (
        "Intro to IAG",
        "Who we are, what we do, and how your role fits in.",
        None,
        None,
    ),
    (
        "Expectations & Operations",
        "How a day runs, what's expected of you, and how we work as a team.",
        None,
        None,
    ),
    (
        "How to Use the Software",
        "A walkthrough of the portal you'll work leads, appointments and deals in.",
        None,
        None,
    ),
    (
        "How to Use HealthSherpa",
        "Creating an application, verifying eligibility, comparing plans and completing the enrollment.",
        "https://vimeo.com/1210418125?fl=pl&fe=sh",
        None,
    ),
    (
        "How to Sell — Script & Compliance",
        "Watch the call from start to finish, then follow the master script below on every call.",
        "https://vimeo.com/1212103306?fl=pl&fe=sh",
        SALES_SCRIPT,
    ),
    (
        "How to Retain Clients & Referrals",
        "Keeping the clients you enroll and turning every call into the next one.",
        "https://vimeo.com/1176657104?fl=pl&fe=sh",
        None,
    ),
    (
        "Practice — Dummy Account",
        "A practice HealthSherpa account to run through the whole process safely before your first live call.",
        None,
        None,
    ),
]
