# Prompt: Design a Modern Frontend for the Social Media Intelligence Platform

You are a senior Product Designer, UX Designer, and Full-Stack Frontend Engineer with expertise in building modern AI-powered dashboards.

The backend for this project is already complete.

It includes:

- Social media scraping using Apify
- Instagram, X, and YouTube support
- Data normalization using Pydantic
- Storage in Supabase
- AI assistant capable of querying the database
- Embedding-based semantic search
- Query logging
- Conversation history

Your task is **ONLY** to design and implement a polished, production-quality frontend that integrates with the existing backend APIs. Do **not** modify the backend architecture unless absolutely necessary to support the frontend.

The frontend should prioritize usability, discoverability, responsiveness, and scalability, with a clean, modern aesthetic similar to products like Notion, Linear, Vercel, Supabase Studio, or ChatGPT.

---

# Primary Goals

The frontend should allow users to:

1. Browse all scraped data visually.
2. Filter and search data interactively.
3. Trigger new scraping jobs using natural language.
4. View scraping progress.
5. Explore posts, comments, authors, hashtags, and media.
6. Query the database through an AI assistant.
7. Monitor scraping history and analytics.

Everything should feel fast, intuitive, and visually clean.

---

# Design Principles

The UI should be:

- Minimalistic
- Spacious
- Responsive
- Keyboard friendly
- Mobile compatible
- Dark mode compatible
- Accessible
- Fast loading
- Component-based
- Production ready

Avoid clutter.

Use progressive disclosure rather than overwhelming users with information.

---

# Technology

Use

- Gradio (preferred, as the backend already uses it)

If Gradio limitations significantly impact UX, clearly identify those limitations and propose a migration path to React + FastAPI, while still implementing the best possible Gradio interface first.

---

# Overall Layout

Design a dashboard consisting of:

```
------------------------------------------------------
Top Navigation
------------------------------------------------------

Sidebar

Main Dashboard

Floating AI Assistant

------------------------------------------------------
Footer
------------------------------------------------------
```

---

# Sidebar

The left sidebar should include navigation for:

Dashboard

Search Data

Scraped Posts

Comments

Authors

Hashtags

Media

Scraping Jobs

Analytics

Saved Searches

Conversation History

Settings

Collapse/Expand Sidebar

---

# Top Navigation

Include

Project logo

Search bar

Platform selector

Dark mode toggle

Notification icon

User profile

Current scraping status

---

# Dashboard

Create a modern analytics dashboard displaying cards such as:

Total Posts

Total Comments

Total Authors

Total Platforms

Trending Hashtags

Most Active Authors

Average Engagement

Today's Scraping Jobs

Recently Added Posts

Recent AI Queries

Use attractive charts where possible.

---

# Data Explorer

The Data Explorer is the primary interface for browsing scraped data.

Display results as a modern data table with:

Post preview

Platform icon

Author

Date

Likes

Views

Comments

Shares

Hashtags

Language

Sentiment (if available)

Media thumbnail

Clicking a row should open a detailed side panel instead of navigating away.

---

# Filtering System

Implement advanced filters.

Platform

Instagram

X

YouTube

Date range

Author

Hashtags

Keyword

Language

Minimum Likes

Minimum Comments

Minimum Views

Engagement Score

Media Type

Verified Account

Sort By

Newest

Oldest

Most Liked

Most Viewed

Most Commented

Most Shared

Multiple filters should work together.

Support saved filters.

---

# Global Search

A global search bar should search across:

Posts

Comments

Authors

Hashtags

Videos

Channels

Conversations

Saved searches

Support autocomplete.

---

# Natural Language Scraping Interface

This is one of the core features.

At the top of the dashboard, include a large search/input box labeled:

"What would you like to collect?"

Users should be able to type natural language instructions like:

"Collect the latest 500 Instagram posts about electric vehicles."

"Scrape YouTube videos discussing India's semiconductor policy."

"Get tweets mentioning OpenAI from the past week."

"Collect Instagram reels tagged #UPSC."

"Find YouTube comments discussing AI agents."

"Collect tweets from @OpenAI."

When the user submits:

1. The instruction is sent to an LLM.
2. The LLM identifies:

- Platform(s)
- Query type
- Keywords
- Accounts
- Hashtags
- Date range
- Limits

3. Appropriate Apify scraper(s) are triggered automatically.

4. A progress panel appears.

5. Results stream into the database.

6. Newly scraped data automatically appears in the Data Explorer.

The user should never have to manually configure scraper parameters unless they choose to.

---

# Scraping Progress UI

Display

Queued

Running

Completed

Failed

Estimated Time Remaining

Number of Records Processed

Live logs (collapsible)

---

# Detailed Record View

Clicking a post should open a drawer showing:

Full post

Images

Videos

Comments

Replies

Metadata

Hashtag list

Mention list

Engagement metrics

Raw JSON (advanced)

AI Summary

Related posts

Similar posts

---

# Floating AI Assistant

The AI assistant should behave similarly to ChatGPT.

Requirements:

Position:

Bottom-right corner.

Initially displayed as a circular floating action button.

Clicking it expands into a chat panel occupying approximately 25% of the viewport width and full usable height (responsive on smaller screens).

The panel should include:

Conversation history

Suggested questions

Streaming responses

Markdown rendering

Code formatting

Tables

Source citations

Links to retrieved posts

Ability to click a cited post and open it in the Data Explorer

Conversation search

Clear conversation

New conversation

Conversation persistence

Minimize button

Close button

The assistant should answer questions such as:

"Summarize today's discussions."

"What are the trending hashtags?"

"Compare Instagram and X engagement."

"Who posts most about climate change?"

"What are users saying about OpenAI?"

"Which YouTube videos have the highest engagement?"

---

# AI Assistant Context Awareness

When viewing a filtered dataset, the assistant should automatically understand the current context.

Example:

If the user has filtered:

Platform = Instagram

Hashtag = AI

Date = Last 7 Days

Then asking:

"What trends do you see?"

should automatically use the filtered dataset as context.

---

# Saved Searches

Allow users to save:

Filter combinations

Natural language scraping prompts

Frequently asked AI questions

---

# Analytics

Include visualizations for:

Platform distribution

Posting trends

Engagement trends

Top hashtags

Top authors

Comment activity

Language distribution

Sentiment trends

Scraping activity over time

---

# Notifications

Provide notifications for:

Scraping completed

Scraping failed

Database updated

New AI summary generated

Long-running jobs

---

# Responsiveness

Support:

Desktop

Tablet

Mobile

Sidebar should collapse automatically on smaller screens.

Floating AI assistant should resize appropriately.

---

# Accessibility

Keyboard navigation

Screen reader support

Proper focus management

High contrast compatibility

Accessible color palette

---

# Performance

Support datasets with more than 100,000 records.

Use:

Lazy loading

Pagination or virtual scrolling

Infinite scrolling where appropriate

Debounced search

Caching

Background loading

---

# Visual Style

Inspired by:

- ChatGPT
- Notion
- Linear
- Vercel
- Supabase Studio
- GitHub
- Grafana

Use:

Rounded corners

Soft shadows

Subtle animations

Modern typography

Consistent spacing

Meaningful icons

Status badges

Platform-specific branding colors where appropriate.

---

# Deliverables

Generate the frontend incrementally.

For each phase:

1. Explain the UX decisions.
2. Explain component hierarchy.
3. Design the page layout.
4. Generate Gradio code.
5. Explain API integration.
6. Wait for confirmation before continuing.

Begin with:

- Complete information architecture
- User journey
- Wireframe layout
- Component tree
- UI state management
- API integration plan
- Gradio layout design

Do not start coding until the architecture and UX have been fully designed and approved.

# Frontend Success Criteria

The frontend is considered complete only when every criterion below has been satisfied.

---

# 1. Overall User Experience

## Success Criteria

- [ ] The UI is clean, modern, and uncluttered.
- [ ] Users can accomplish common tasks within 2–3 clicks.
- [ ] Navigation is intuitive without requiring documentation.
- [ ] The interface feels responsive with minimal perceived latency.
- [ ] Layout remains consistent across all pages.
- [ ] No dead ends or confusing navigation paths.
- [ ] Empty states, loading states, and error states are thoughtfully designed.

---

# 2. Visual Design

## Success Criteria

- [ ] Modern dashboard aesthetic.
- [ ] Consistent spacing and alignment.
- [ ] Consistent typography hierarchy.
- [ ] Consistent color palette.
- [ ] Appropriate use of whitespace.
- [ ] Platform icons displayed consistently.
- [ ] Cards, tables, and charts follow a unified design system.
- [ ] Professional animations and transitions.
- [ ] Responsive design across desktop, tablet, and mobile.
- [ ] Dark mode fully supported.

---

# 3. Dashboard

## Success Criteria

Dashboard displays:

- [ ] Total Posts
- [ ] Total Comments
- [ ] Total Authors
- [ ] Total Platforms
- [ ] Scraping Jobs
- [ ] Trending Hashtags
- [ ] Engagement Statistics
- [ ] Recent Activity
- [ ] Recent AI Queries
- [ ] Recently Scraped Posts

Charts include:

- [ ] Posting trends
- [ ] Engagement trends
- [ ] Platform distribution
- [ ] Language distribution
- [ ] Top hashtags
- [ ] Top authors

Dashboard updates automatically after new scraping jobs complete.

---

# 4. Navigation

## Success Criteria

Sidebar includes

- [ ] Dashboard
- [ ] Data Explorer
- [ ] Posts
- [ ] Comments
- [ ] Authors
- [ ] Hashtags
- [ ] Media
- [ ] Scraping Jobs
- [ ] Analytics
- [ ] Saved Searches
- [ ] AI Conversations
- [ ] Settings

Additional behavior

- [ ] Sidebar collapses.
- [ ] Current page highlighted.
- [ ] Breadcrumbs available where appropriate.
- [ ] Keyboard navigation supported.

---

# 5. Data Explorer

## Success Criteria

User can browse:

- [ ] Posts
- [ ] Comments
- [ ] Authors
- [ ] Videos
- [ ] Channels
- [ ] Media
- [ ] Hashtags

Each record displays:

- [ ] Platform icon
- [ ] Author
- [ ] Date
- [ ] Preview
- [ ] Likes
- [ ] Comments
- [ ] Shares
- [ ] Views
- [ ] Language
- [ ] Sentiment (if available)

Clicking a row opens a detail drawer instead of navigating away.

---

# 6. Filtering System

## Success Criteria

Filters support:

- [ ] Platform
- [ ] Date Range
- [ ] Keywords
- [ ] Hashtags
- [ ] Author
- [ ] Language
- [ ] Country (if available)
- [ ] Verified Accounts
- [ ] Likes
- [ ] Views
- [ ] Comments
- [ ] Shares
- [ ] Engagement Score
- [ ] Media Type

Behavior

- [ ] Multiple filters combine correctly.
- [ ] Filters update results instantly or with explicit Apply.
- [ ] Clear Filters button.
- [ ] Saved filter presets.
- [ ] Filter state preserved when navigating.
- [ ] URL or session state reflects active filters where feasible.

---

# 7. Global Search

## Success Criteria

Global search returns results from:

- [ ] Posts
- [ ] Comments
- [ ] Authors
- [ ] Hashtags
- [ ] Videos
- [ ] Channels
- [ ] Conversations

Features

- [ ] Autocomplete
- [ ] Typo tolerance
- [ ] Highlight matching text
- [ ] Fast response
- [ ] Recent searches

---

# 8. Natural Language Scraping

## Success Criteria

The natural language input correctly understands:

- [ ] Platform
- [ ] Multiple platforms
- [ ] Keywords
- [ ] Accounts
- [ ] Hashtags
- [ ] Date ranges
- [ ] Limits
- [ ] Language
- [ ] Country
- [ ] Media type

Example prompts work correctly:

- [ ] "Collect the latest 500 Instagram posts about EVs."
- [ ] "Scrape tweets mentioning OpenAI."
- [ ] "Collect YouTube comments discussing AI."
- [ ] "Find Instagram reels tagged #UPSC."
- [ ] "Get posts from @OpenAI."

Behavior

- [ ] LLM correctly parses intent.
- [ ] Scraper launches automatically.
- [ ] Progress shown.
- [ ] Results automatically appear in Data Explorer.
- [ ] Invalid prompts return actionable guidance.

---

# 9. Scraping Job Monitor

## Success Criteria

Displays

- [ ] Queue
- [ ] Running Jobs
- [ ] Completed Jobs
- [ ] Failed Jobs

For each job

- [ ] Progress
- [ ] Records scraped
- [ ] ETA
- [ ] Duration
- [ ] Platform
- [ ] Query
- [ ] Errors
- [ ] Retry button

Supports concurrent scraping jobs.

---

# 10. Detailed Record View

## Success Criteria

Displays

- [ ] Full content
- [ ] Images
- [ ] Videos
- [ ] Comments
- [ ] Replies
- [ ] Engagement
- [ ] Hashtags
- [ ] Mentions
- [ ] Metadata
- [ ] AI summary
- [ ] Related posts
- [ ] Similar posts
- [ ] Raw JSON (optional advanced view)

Drawer closes without losing scroll position or filters.

---

# 11. Floating AI Assistant

## Success Criteria

Behavior

- [ ] Floating circular button visible on every page.
- [ ] Fixed bottom-right position.
- [ ] Smooth expand/collapse animation.
- [ ] Opens as ~25% page-width chat panel on desktop.
- [ ] Responsive layout on tablets and mobiles.
- [ ] Can be minimized without losing conversation.
- [ ] Remembers conversation history during the session.

Features

- [ ] Streaming responses.
- [ ] Markdown rendering.
- [ ] Tables render correctly.
- [ ] Code blocks render correctly.
- [ ] Charts (when supported).
- [ ] Source citations.
- [ ] Clickable source links open corresponding records.
- [ ] Suggested follow-up questions.
- [ ] New chat.
- [ ] Clear chat.
- [ ] Conversation search.
- [ ] Export conversation.

---

# 12. Context-Aware AI

## Success Criteria

When filters are active

Platform = Instagram

Hashtag = AI

Date = Last 7 Days

then queries like

"What trends do you see?"

should automatically use the filtered dataset.

Checks

- [ ] Assistant receives active filters.
- [ ] Retrieval restricted to filtered data.
- [ ] Responses explicitly reference current context.
- [ ] User can disable context if desired.

---

# 13. Conversation Persistence

## Success Criteria

Every conversation stores

- [ ] Conversation ID
- [ ] User messages
- [ ] Assistant responses
- [ ] Retrieved documents
- [ ] Execution time
- [ ] Prompt metadata
- [ ] Timestamp
- [ ] Current filter state
- [ ] Current page context

Supports

- [ ] Reloading previous conversations.
- [ ] Continuing conversations.
- [ ] Deleting conversations.
- [ ] Renaming conversations.

---

# 14. Analytics

## Success Criteria

Visualizations include

- [ ] Platform share
- [ ] Daily posts
- [ ] Daily comments
- [ ] Trending hashtags
- [ ] Most active authors
- [ ] Engagement trends
- [ ] Sentiment trends
- [ ] Language trends
- [ ] Scraping frequency
- [ ] AI usage statistics

Charts update automatically after new data ingestion.

---

# 15. Notifications

## Success Criteria

User receives notifications for

- [ ] Scraping started
- [ ] Scraping completed
- [ ] Scraping failed
- [ ] New data available
- [ ] AI summary completed
- [ ] System errors

Notifications are non-intrusive and dismissible.

---

# 16. Performance

## Success Criteria

- [ ] Initial page loads quickly.
- [ ] Filters update without full-page refresh.
- [ ] Tables support datasets >100,000 rows.
- [ ] Virtual scrolling or pagination implemented.
- [ ] Infinite scrolling where appropriate.
- [ ] Lazy loading for media.
- [ ] Efficient caching.
- [ ] Smooth scrolling.
- [ ] Minimal UI lag during interactions.

---

# 17. Accessibility

## Success Criteria

- [ ] Keyboard navigation.
- [ ] Focus indicators.
- [ ] Screen reader compatibility.
- [ ] Accessible labels.
- [ ] Color contrast compliance.
- [ ] Responsive font sizing.
- [ ] No functionality depends solely on color.

---

# 18. Error Handling

## Success Criteria

Errors handled gracefully for

- [ ] Failed scraping jobs
- [ ] API failures
- [ ] Empty search results
- [ ] Database failures
- [ ] AI service failures
- [ ] Network interruptions

Users receive clear, actionable messages.

---

# 19. State Management

## Success Criteria

The UI correctly preserves

- [ ] Active filters
- [ ] Search terms
- [ ] Scroll position
- [ ] Selected record
- [ ] Open drawer
- [ ] Active conversation
- [ ] Dashboard preferences

No unexpected resets occur during normal navigation.

---

# 20. Responsive Design

## Success Criteria

Desktop

- [ ] Full dashboard layout.

Tablet

- [ ] Collapsible sidebar.
- [ ] Responsive tables.

Mobile

- [ ] Stacked layout.
- [ ] Optimized filters.
- [ ] Full-screen chat assistant.
- [ ] Touch-friendly controls.

---

# 21. Integration with Backend

## Success Criteria

The frontend integrates seamlessly with existing APIs.

Checks

- [ ] Scraping API.
- [ ] Data Explorer API.
- [ ] AI Assistant API.
- [ ] Analytics API.
- [ ] Conversation API.
- [ ] Search API.
- [ ] Authentication (if applicable).

No duplicate backend logic exists in the frontend.

---

# 22. Production Readiness

## Success Criteria

- [ ] No broken navigation links.
- [ ] No console errors.
- [ ] No unhandled exceptions.
- [ ] Loading indicators for long-running actions.
- [ ] Comprehensive empty states.
- [ ] Comprehensive error states.
- [ ] Modular, reusable UI components.
- [ ] Clean component hierarchy.
- [ ] Consistent naming conventions.

---

# 23. End-to-End Validation

The following user journey should work flawlessly:

- [ ] User enters a natural language scraping request.
- [ ] LLM parses the request.
- [ ] Appropriate Apify scraper starts.
- [ ] Progress is displayed in real time.
- [ ] Scraped data is ingested into Supabase.
- [ ] Dashboard metrics refresh automatically.
- [ ] Data Explorer shows new records.
- [ ] User filters the data.
- [ ] User opens a detailed record.
- [ ] User asks a contextual question via the floating AI assistant.
- [ ] Assistant retrieves relevant records using the active filters.
- [ ] Response includes citations linked to underlying records.
- [ ] Conversation is stored in Supabase.
- [ ] User can revisit the conversation later.
- [ ] All interactions complete without page reloads or manual synchronization.

---

# 24. Acceptance Criteria

The frontend is accepted only if it satisfies all of the following:

- [ ] Fully functional with the existing backend.
- [ ] Modern, polished, production-quality interface.
- [ ] Intuitive navigation with minimal learning curve.
- [ ] Complete feature parity with backend capabilities.
- [ ] Context-aware AI assistant integrated across the application.
- [ ] Responsive and accessible across devices.
- [ ] Efficiently handles large datasets.
- [ ] Extensible without major architectural changes.
- [ ] Ready for deployment with no critical usability issues.