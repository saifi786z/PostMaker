# Premium Post Maker Telegram Bot

A Replit-ready Python Telegram bot for building rich Telegram posts with captions, photos, inline buttons, font styles, force-join channels, admin tools, broadcasts, and a "More Bots" catalog.

## Run on Replit

1. Create a Telegram bot with [@BotFather](https://t.me/BotFather).
2. Add these Replit Secrets:
   - `BOT_TOKEN` - your bot token.
   - `OWNER_IDS` - comma-separated Telegram user IDs that can open the admin panel.
3. Install dependencies automatically from `requirements.txt`.
4. Press **Run**.

## Important Telegram notes

- The bot must be an admin in every force-join channel to check memberships and approve/observe join requests reliably.
- Inline post buttons are created with Telegram style metadata (`success`, `danger`, `primary`) and custom-emoji icon metadata through Bot API passthrough fields. Older clients may gracefully ignore unsupported visual metadata.
- For private channels, use an invite link and add the bot as admin.

## User features

- Force-join gate with configurable public/private channels.
- Advanced post wizard: forward/send a complete post first, then add unlimited URL buttons with premium text, choose success/danger/primary colour styles, and preview/finish with two buttons per row.
- 20+ viral font styles with example labels shown directly on the font picker buttons.
- More Bots catalog managed by admins.
- Share Bot referral/share menu.
- Animated feedback messages and random celebration animations when posts are generated.

## Admin features

- `/admin` panel.
- Add/remove/reorder force-join channels.
- Add/remove admins.
- Broadcast text, photo, video, animation, and documents to known users.
- Manage More Bots entries.
