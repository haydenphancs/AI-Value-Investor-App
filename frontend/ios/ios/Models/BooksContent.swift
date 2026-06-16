//
//  BooksContent.swift
//  ios
//
//  Real Book-Core detail content + core lists for the Book Library, imported VERBATIM from
//  documents/books/<Book>/core N.txt via backend/scripts/gen_books_swift.py. Keyed by
//  LibraryBook.curriculumOrder. Covers the whole Book Library (orders 1...10).
//  Do not hand-edit — regenerate from source.
//

import Foundation

extension CoreChapterContent {
    /// Core detail content per book, keyed by curriculumOrder then core number.
    static let booksByOrder: [Int: [Int: CoreChapterContent]] = [
        1: [
            1: CoreChapterContent(
                chapterNumber: 1,
                chapterTitle: "De-Programming the \"Employee\" Mindset",
                bookTitle: "Rich Dad Poor Dad",
                bookAuthor: "Robert T. Kiyosaki",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Hamster Wheel of Emotion")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("You wake up, you go to work, you pay your bills, you wait for the weekend. Repeat until you die. This isn't just a routine; it’s a trap designed by your own biology. Most people are driven by two primal emotions: Fear (of not having money) and Greed (the desire for a better lifestyle). When you get a raise, the Fear temporarily subsides, but Greed immediately kicks in, whispering that you need a new car, a bigger house, or that luxury vacation. So, you spend the raise. The Fear returns. You run faster. You are in the Rat Race, and the friction burning you out isn't your boss—it’s your own emotional reaction to money.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The 10-Cent Lesson")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author tells the story of being nine years old and making a deal with his \"Rich Dad.\" He agrees to work in a convenience store for 10 cents an hour. He slaves away for three weeks, dusting cans, hating the work, and feeling exploited. Finally, he marches to his Rich Dad to quit, demanding a raise.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Rich Dad smiles. \"Now you sound like most of my employees,\" he says. He explains that if he had just given the boy a raise, he would have learned nothing but how to be a better employee. Instead, Rich Dad offers him a new deal: \"I’ll teach you, but I won’t pay you at all.\" By taking the paycheck away, the boy was forced to stop thinking like a worker looking for a wage and start thinking like an owner looking for an opportunity. His brain, no longer sedated by a salary, eventually spotted a business opportunity right in the store—a comic book library—that made him far more than 10 cents.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The 2026 Trap")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In 2026, the Rat Race has upgraded. It’s no longer just about paying bills; it’s about the \"Golden Handcuffs\" of remote work comfort and the subtle threat of AI displacement. The \"safe job\" is a myth. If your primary income relies on trading hours for dollars, you are essentially shorting your own future. The modern employee mindset says, \"I need to learn prompt engineering so I don't get fired.\" The wealthy mindset says, \"I will build an AI agent that does the work for me while I sleep.\" The trap today is thinking that \"upskilling\" for a salary is freedom. It is not. It is just a shinier wheel.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The Emotion Audit",
                                description: "Tomorrow, when you feel the urge to buy something \"nice,\" stop. Ask: Is this desire, or is this boredom? When you feel the urge to work overtime, ask: Is this ambition, or is this fear of your boss? Name the emotion to break the loop.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The \"Zero-Pay\" Protocol",
                                description: "Dedicate 5 hours this week to a project that pays you exactly $0 right now but builds an asset (a blog, a code repository, a network). Train your brain to work for assets, not cash.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 199,
                currentProgress: 0.0
            ),
            2: CoreChapterContent(
                chapterNumber: 2,
                chapterTitle: "Mastering the Financial Scorecard",
                bookTitle: "Rich Dad Poor Dad",
                bookAuthor: "Robert T. Kiyosaki",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Illusion of Wealth")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is the secret that keeps the middle class exhausted: You are looking at the wrong scoreboard. Most professionals believe that a high salary equals wealth. It doesn’t. You can earn $250,000 a year and still be technically insolvent if your monthly burn rate matches your income. The friction isn't your paycheck; it’s your financial literacy. You have been trained to read words, but you haven't been trained to read numbers. Consequently, you spend your life building someone else's business (your boss's), buying someone else's investments (the bank's), and paying someone else's bills (the government's).")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Tale of Two Columns")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author illustrates this with a simple, brutal truth that shattered the worldview of a young couple. They celebrate a pay raise by buying their \"dream home.\" They proudly list this house under the \"Asset\" column of their financial statement.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But the author's mentor, the \"Rich Dad,\" draws a simple diagram to prove them wrong. He defines an asset not by accounting tradition, but by the direction of cash flow. An asset puts money in your pocket. A liability takes money out.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Because that house requires a mortgage, property taxes, insurance, and maintenance, cash is flowing out. Therefore, the house is a liability. The \"Rich Dad\" explains that the poor work for money to pay expenses; the middle class buys liabilities they think are assets (like houses and cars); but the rich focus entirely on the Asset Column—acquiring things that generate cash while they sleep.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The 2026 Camouflage")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In 2026, the \"Liability Trap\" is even more sophisticated. It’s no longer just a house; it’s digital. It is the crypto \"investment\" that has zero utility and generates no yield—that is speculation, not an asset. It is the five different AI software subscriptions you pay for monthly but don’t use to generate income.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the modern era, true assets have evolved. An asset might be a high-yield DeFi staking protocol, a dividend-paying ETF, or a piece of code you wrote once that sells itself repeatedly. Conversely, \"Buy Now, Pay Later\" schemes for consumer goods are the modern shackles. The principle remains: If you have to work to keep it, it's a job. If you have to pay to keep it, it's a liability. If it pays you to keep it, it's an asset.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The Ruthless Audit",
                                description: "Tonight, draw a T-chart. List everything you own. If it requires money to maintain and yields zero cash flow (your car, your house, your unused tech), move it to the Liability column immediately. Stop lying to yourself.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The Replacement Ratio",
                                description: "For every new liability you want (e.g., a new phone), you must first buy an asset that covers the monthly cost of that liability. Do not buy the toy until the asset buys it for you.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 189,
                currentProgress: 0.0
            ),
            3: CoreChapterContent(
                chapterNumber: 3,
                chapterTitle: "The Corporate Shield Strategy",
                bookTitle: "Rich Dad Poor Dad",
                bookAuthor: "Robert T. Kiyosaki",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Government's First Bite")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is the raw deal you signed up for without reading the fine print: You work for money, the government takes a huge bite (taxes), and you try to live on what’s left. The harder you work, the more they take. It’s a rigged game. Most people think tax law is a punishment for making money. It isn’t. It’s an incentive system designed by the wealthy to reward the wealthy. If you are an employee, you are playing the game on \"Hard Mode.\" You earn, you get taxed, and then you spend. You are paying the bill for everyone else's roads and schools before you even buy your own groceries.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Magic Folder")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author reveals a secret that completely demystifies the word \"Corporation.\" He explains that a corporation isn't a factory with smokestacks or a skyscraper with a logo. It is simply a file folder with some legal documents in it. It is a legal entity that creates a body without a soul.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Why does this matter? Because this \"body\" has magic powers. The rich use this legal shield to flip the script on the government. An individual earns, pays tax, and spends what is left. A corporation earns, spends everything it can (expenses), and then pays tax on what is left. The author tells the story of how he uses his corporation to pay for his car, his \"board meetings\" in Hawaii, and his health club membership—all with pre-tax dollars. The corporation is the shield that protects his wealth from the taxman’s first bite.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Solopreneur's Edge")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In 2026, you don't need a boardroom to have a corporation. You just need a laptop. The \"Gig Economy\" and remote work have democratized the Corporate Shield. If you are a freelancer, a content creator, or a consultant, you are a business. That high-end laptop you use for coding? That’s a business expense. That trip to a tech conference in Tokyo? That’s travel and education. Even a portion of your home internet and rent can be a write-off if you have a compliant home office. The modern wealthy don't evade taxes; they just use the incentives (like depreciation on real estate or R&D credits for AI development) that the government wants them to use.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Side Hustle\" Shift",
                                description: "Stop calling it a \"hobby.\" If you make money from it, treat it as a business immediately. Open a separate bank account for it tomorrow.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The Pre-Tax Mindset",
                                description: "Before you spend a dollar on technology, travel, or education, ask: \"Can my business buy this for me?\" If the answer is yes, buy it through the business account. Legally, of course.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 180,
                currentProgress: 0.0
            ),
            4: CoreChapterContent(
                chapterNumber: 4,
                chapterTitle: "The Opportunity Hunter",
                bookTitle: "Rich Dad Poor Dad",
                bookAuthor: "Robert T. Kiyosaki",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"I Can't\" Paralysis")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Most people are blind. They walk down the street and see buildings, businesses, and people. A wealthy person walks down the same street and sees deals. The friction for the average person is a mental firewall: the belief that \"I don't have the money\" is a stop sign. It isn’t; it’s a question. When you say \"I can't afford it,\" your brain shuts down. You stop looking. You become a victim of the economy rather than a master of it. The failure here isn't a lack of resources; it's a lack of imagination.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Phoenix Fire Sale")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author illustrates this with a story from the recession. While everyone else was screaming that the sky was falling, the author saw a clearance sale. He recounts finding a house in Phoenix during a market crash. The home was worth $65,000, but the owner was desperate, and the bank was foreclosing. The price? $20,000.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is the twist: The author didn’t have $20,000.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But he didn't let that stop him. He borrowed the $2,000 down payment from a friend for 90 days for $200 interest. He bought the house, advertised it, and sold it for $60,000 within a few weeks. He paid back the friend, paid the bank, and pocketed $40,000. He didn’t work for that money; he invented it. He used his financial intelligence to bridge the gap between a seller in pain and a buyer in need.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The 2026 Arbitrage")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In 2026, you don't need to drive around Phoenix to find deals; the opportunities are digital and move at the speed of light. The \"distressed seller\" might be a tech startup liquidating assets, or a mispriced token in a DeFi liquidity pool. The \"house\" might be an undervalued domain name, a neglected newsletter with 10,000 subscribers, or an inefficiency in an AI workflow that you can automate and sell.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, \"inventing money\" means spotting informational asymmetry. It’s realizing that Company A needs a specific dataset that Company B gives away for free, and you become the bridge. It’s using an AI agent to analyze thousands of real estate listings or crypto pairs instantly to find the one that is mispriced. The principle is the same: You are not buying with cash; you are buying with insight.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"100-10-3-1\" Rule",
                                description: "Stop looking for the \"perfect\" deal. Look for volume. For every 100 properties (or stocks, or side hustles) you analyze, you will make offers on 10. You will get accepted on 3. You will actually buy 1. Start the 100 today.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The \"No Money\" Drill",
                                description: "Find an asset you want (a stock, a course, a piece of equipment). Now, force your brain to list 5 ways to acquire it without using your current salary. (e.g., Barter, affiliate sales, finding a partner, pre-selling a service).",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 190,
                currentProgress: 0.0
            ),
            5: CoreChapterContent(
                chapterNumber: 5,
                chapterTitle: "Trading Security for Skills",
                bookTitle: "Rich Dad Poor Dad",
                bookAuthor: "Robert T. Kiyosaki",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Specialist's Curse")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is the lie that universities sell you: \"Specialize to succeed.\" They tell you to become the best neurosurgeon, the sharpest coder, or the most nuanced accountant. The friction is that specialization is a form of dependency. When you know more and more about less and less, you become a cog that only fits into one specific machine. If that machine breaks (or the industry shifts), you are obsolete. Most people fail to build wealth because they are too busy protecting their \"career path\" to notice they are walking off a cliff. They cling to job security, unaware that \"JOB\" is an acronym for \"Just Over Broke.\"")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"Best-Selling\" Author")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author illustrates this with a stinging encounter with a talented young newspaper reporter. She had a Master’s degree in English and wrote beautifully, but she was struggling to make a living from her novels. She asked the author for advice. His answer? \"Go take a sales training course.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("She was insulted. She packed her briefcase, snapping, \"I am a serious writer, not a used-car salesman.\" The author gently pointed to a book on the table. He said, \"Look at the cover. It says 'Best-Selling Author,' not 'Best-Writing Author'.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He explained that the world is full of talented, poor people. They are one skill away from great wealth. The reporter was a master of the product (writing) but ignored the system (selling). The author urges you to seek work for what you will learn, not what you will earn. He joined the Marine Corps to learn leadership and Xerox to learn sales—skills that became the foundation of his empire.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The 2026 \"Full Stack\" Human")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In 2026, the danger of specialization is existential. AI Agents can now write the code, draft the legal contract, and generate the marketing copy. If you are just a \"writer\" or just a \"coder,\" you are competing with a bot that costs pennies.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The \"Wiser\" strategy today is to become a \"Full Stack\" operator. You don't need to be the best at any one thing; you need to be in the top 25% of three things that rarely go together—like Finance + Design + AI Prompting. That unique intersection is where your value lies. The modern wealthy treat their day jobs as paid apprenticeships. They rotate roles not because they are flaky, but because they are acquiring the infinity stones of business: Management of Cash Flow, Management of Systems, and Management of People.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Anti-Comfort\" Move",
                                description: "Identify the one department in your company you understand the least (usually Sales or Accounting). Take the head of that department to lunch tomorrow. Ask them: \"What is the number one problem you are trying to solve right now?\" Listen.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The Skill Swap",
                                description: "Do not quit your job yet. instead, volunteer for a project that forces you to learn a skill you hate. If you are a quiet coder, force yourself to pitch the project to stakeholders. If you are a talkative salesperson, force yourself to learn the CRM data analytics.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 205,
                currentProgress: 0.0
            ),
            6: CoreChapterContent(
                chapterNumber: 6,
                chapterTitle: "Conquering the Inner Saboteur",
                bookTitle: "Rich Dad Poor Dad",
                bookAuthor: "Robert T. Kiyosaki",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"What If\" Paralyzing Agent")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is the tragedy: You have the financial literacy. You understand assets vs. liabilities. You see the deal. And then… you freeze. The friction isn't the market; it’s the voice in your head screaming, \"What if the tenant leaves?\" \"What if the crypto market crashes?\" \"What if I lose my job?\" This is the \"Inner Saboteur.\" It turns smart people into cowards and keeps the middle class safely poor. The problem is that your brain is wired to survive, not to thrive. It treats a financial risk like a saber-toothed tiger.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Colonel and the Alamo")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author confronts this fear—the fear of losing money—head-on. He tells the story of the Texans and the Alamo. When the Alamo fell, everyone died. It was a tragic, total defeat. But Texans didn’t bury their heads in shame. They shouted, \"Remember the Alamo!\" and used that failure as the fuel to win their independence.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He contrasts this with the \"Chicken Littles\" of the world—the cynics who run around yelling, \"The sky is falling!\" whenever there is a rumor of a recession. He explains that cynics criticize, but winners analyze. Criticism blinds you; analysis opens your eyes. He tells the story of finding a great real estate deal, only to have a \"smart\" friend talk him out of it because \"prices might drop.\" The prices went up, and the friend stayed poor. The lesson? \"Don't let the noise of the world drown out the whisper of opportunity.\"")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Algorithmic Doomer")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In 2026, the \"Chicken Littles\" have a megaphone. They are the algorithmic feeds on your phone, optimized to keep you terrified because fear drives engagement. The \"Inner Saboteur\" is now powered by AI-generated deepfakes and 24/7 \"Crash Coming Soon\" YouTube thumbnails.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Laziness has also evolved. Today, laziness doesn't look like sleeping on the couch; it looks like \"busyness.\" You are too busy answering emails, checking Slack, and \"researching\" (watching tutorials) to actually do anything. This is \"productive procrastination.\" The modern winner ignores the comment section. They realize that if an investment feels \"safe\" to the herd, it’s already too late. The Saboteur tells you to wait for certainty. The Wiser investor knows certainty is expensive.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The Information Diet",
                                description: "Tonight, unsubscribe from every financial news alert and \"market crash\" YouTuber. They are selling you fear, not advice. If they were right, they would be billionaires, not content creators.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The \"24-Hour\" Rule",
                                description: "When you find a potential investment, give yourself exactly 24 hours to analyze it. If you haven't made a \"Go/No-Go\" decision by then, the answer is \"No.\" Indecision is the Saboteur's favorite weapon. Kill it with a deadline.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 179,
                currentProgress: 0.0
            ),
            7: CoreChapterContent(
                chapterNumber: 7,
                chapterTitle: "The \"First 3 Steps\" Launchpad",
                bookTitle: "Rich Dad Poor Dad",
                bookAuthor: "Robert T. Kiyosaki",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Knowledge Coma")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is the final, most dangerous trap: You are smarter now. You have read the Cores. You understand the math. And yet, tomorrow morning, you will likely wake up and do exactly what you did today. This is the \"Knowledge Coma.\" The friction isn't ignorance anymore; it is inertia. Most people collect financial advice like baseball cards—they categorize it, admire it, and show it off, but they never play the game. They are waiting for the \"perfect time\" or the \"perfect deal,\" unaware that perfection is just procrastination in a tuxedo.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Power of \"I Don't Want\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author shatters this paralysis not with a spreadsheet, but with a piece of paper. He tells the story of how he found his \"Why\"—the fuel that outlasted his fear. He didn't start by writing down \"I want to be rich.\" That was too vague. Instead, he sat down and ruthlessly listed what he didn't want.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("\"I don't want to work for someone else for the rest of my life.\" \"I don't want to be told when I can go on vacation.\" \"I don't want to miss my child’s soccer game because of a conference call.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He explains that this \"List of Don'ts\" created a pressure cooker of emotion. It wasn't just a wish; it was a rejection of mediocrity. This deep-seated emotional leverage—the \"Power of Spirit\"—is the only force strong enough to push you through the inevitable obstacles of the first 90 days. Without a burning \"Why,\" the \"How\" is useless.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The 2026 \"Micro-Step\" Revolution")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In 2026, the barrier to entry has collapsed. You don't need $50,000 for a down payment or a license to trade. The \"Launchpad\" today is built on micro-actions. The author couldn't buy 0.0001% of a building in 1997. Today, you can. You can buy fractional shares of real estate, tokenized gold, or a slice of a startup for the price of a latte.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The modern error is thinking you need to \"launch\" a massive enterprise. You don't. You need to launch a habit. The \"Wiser\" approach uses automation to bypass your willpower entirely. You don't \"decide\" to invest; you set up a smart contract or an auto-debit that invests for you before you even wake up. You build the wealth architecture once, and then you let the machine run.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Anti-Vision\" Board",
                                description: "Tonight, write down 5 things you hate about your current financial life. (e.g., \"I hate the anxiety of checking my bank balance\"). Tape this to your bathroom mirror. Anger is a better fuel than hope for the first mile.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The $50 Commitment",
                                description: "Do not wait for $10,000. Tomorrow morning, open a brokerage or crypto account and buy $50 of any asset you have researched. The amount doesn't matter; breaking the seal of \"Consumer\" to become an \"Investor\" matters.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The \"2-Book\" Diet",
                                description: "Stop reading news. For the next month, read only biographies of wealthy people. You need to normalize their thinking in your brain until it feels weird not to own assets.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 204,
                currentProgress: 0.0
            ),
        ],
        2: [
            1: CoreChapterContent(
                chapterNumber: 1,
                chapterTitle: "Drawing the Battle Lines",
                bookTitle: "The Intelligent Investor",
                bookAuthor: "Benjamin Graham",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Illusion of Competence")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is the uncomfortable truth: 90% of people who think they are \"investing\" are actually just gambling with better odds. The friction in your financial life usually stems from a single category error. You treat the stock market like a casino where the chips are expensive and the rules are vague. You assume that because you bought a stock and held it for a year, you are an investor. You aren't. If you bought it based on a feeling, a tip, or a chart pattern, you are speculating. And the market loves to punish speculators who believe they are investors.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Trinity of Safety")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author solves this by demanding we sign a mental contract before spending a dime. He strips away the noise and offers a ruthless definition. An operation is only an investment if it meets three specific criteria upon thorough analysis:")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Safety of Principal: You are reasonably sure you won't lose your initial cash.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Adequate Return: You aren't looking for a lottery ticket; you are looking for a fair rent on your money.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Thorough Analysis: You have done the math on the underlying business, not just the stock price.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Imagine a man betting on a horse race. He studies the track record, the jockey, and the weather. He is informed, but he is still gambling because he has no safety of principal—if the horse loses, his money vanishes. The \"Intelligent Investor\" refuses to bet on the horse; he buys the racetrack.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Gamification Trap")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the modern era, this distinction is harder to see because the casino has moved into your pocket. Trading apps shower you with digital confetti when you execute a trade, conditioning you to crave action over analysis. We see this in the \"Crypto Craze\" or the \"AI Gold Rush.\" Buying a digital token because you hope a billionaire tweets about it is not investing; it is the \"Greater Fool Theory\"—hoping someone else will pay more for your mistake. The danger today isn't just market volatility; it’s the user interface of your life designed to make speculation feel like strategy. If your asset relies entirely on hype to succeed, you aren't investing in a business; you're investing in a narrative.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The 10% Containment Wall",
                                description: "Take your total capital. Segregate a maximum of 10% into a separate \"Mad Money\" account. This is the only place you are allowed to speculate on high-risk assets, crypto, or hunches.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The \"Why\" Audit",
                                description: "Open your portfolio. For every single line item, write down the reason you own it. If your reason includes the words \"hype,\" \"moon,\" or \"everyone else is,\" move that asset immediately to the Mad Money containment zone.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 182,
                currentProgress: 0.0
            ),
            2: CoreChapterContent(
                chapterNumber: 2,
                chapterTitle: "The Invisible Enemy",
                bookTitle: "The Intelligent Investor",
                bookAuthor: "Benjamin Graham",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Safety Paradox")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The greatest trick the financial industry ever pulled was convincing you that cash is \"safe.\" You look at your bank account, and the number stays the same or grows slightly. You feel a warm sense of security. You sleep well. But this is a hallucination. In the physical world, if you left a car in a garage for twenty years, you’d expect it to rust. Money is no different, yet we treat it as if it’s immune to decay. The friction here is psychological: we are wired to fear the volatility of the stock market (where prices bounce up and down visibly), so we run into the arms of a guaranteed loss. We choose the slow bleed over the bumpy ride.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Yardstick That Shrank")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author illustrates this with a brutal reality check from the first half of the 20th century. He tells the story of the \"Conservative Investor\" who, traumatized by market crashes, put 100% of his wealth into high-grade bonds. This man felt prudent. He collected his fixed interest checks like clockwork. But while he was staring at the stable numbers on his statements, the world around him changed.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The price of bread, housing, and energy doubled. The \"yardstick\" of money had shrunk. The author points out that by the time this investor needed to spend his principal, it had lost half its purchasing power. The man hadn't lost dollars; he had lost lifestyle. The \"safe\" investment was actually a trap that guaranteed he would be poorer at the end than when he started. The author’s lesson is stark: Inflation is a thief that works in the dark, and keeping all your money in \"safe\" cash is not prudence—it is a decision to let the thief empty your house while you watch.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The High-Yield Hallucination")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, this enemy is even more deceptive. You open a High-Yield Savings Account (HYSA) offering 5% and think you are winning. You aren’t. You are barely treading water. If inflation is running at 3.5% and the tax man takes another 1.5% of your gains, your real return is zero. You are running on a financial treadmill just to stay in the same place.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Furthermore, the \"basket of goods\" has changed. The author worried about the price of milk; you need to worry about the cost of healthcare, college tuition, and prime real estate, which often inflate significantly faster than the government's official CPI numbers. If you are paying 1% to a financial advisor to manage a portfolio of cash and bonds that yields 4%, you are essentially paying someone to manage your slow bankruptcy. In a world of unlimited fiat currency printing, holding cash is like holding a melting ice cube. You cannot save your way to wealth in a currency that is designed to depreciate.")
                    ),
                ],
                audioDurationSeconds: 189,
                currentProgress: 0.0
            ),
            3: CoreChapterContent(
                chapterNumber: 3,
                chapterTitle: "Mastering the Manic-Depressive Market",
                bookTitle: "The Intelligent Investor",
                bookAuthor: "Benjamin Graham",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Price Tag Panic")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Imagine you own a house you love. You know its value: good roof, solid foundation, great neighborhood. Now imagine a stranger stands on your lawn every single morning yelling a different price at you. Monday he screams, \"I'll give you $500,000!\" Tuesday he whispers, \"The market is crashing! I'll only give you $150,000!\" Wednesday he is euphoric again and offers $900,000.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Most people would close the blinds and ignore him. But in the stock market, you do the opposite. You let this stranger dictate your emotional state. When he yells a low price, you panic and sell your house cheap. When he yells a high price, you feel rich and buy more houses. This is the friction: you confuse the price on the screen with the value of the business. You let the fluctuating price tag determine your worth, rather than the quality of the merchandise.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Partner You Can't Fire")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author introduces us to this stranger. His name is \"Mr. Market.\" He is your business partner in a private company. Mr. Market is obliging but emotionally unstable. Every day, without fail, he names a price at which he will either buy your interest or sell you his.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The catch? His price has nothing to do with the business's actual performance. It is based entirely on his mood. When he is manic (optimistic), he sees only sunshine and names a high price. When he is depressed (pessimistic), he sees only trouble and names a low price. The author’s solution is a mindset shift: Mr. Market is there to serve you, not to guide you. You are not forced to deal with him. If his offer is ridiculous, you ignore him. If his offer is a bargain, you buy. If his offer is absurdly high, you sell. You are the adult in the room; he is the hysterical child.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Algorithm of Anxiety")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, Mr. Market isn't just a guy on your lawn; he is in your pocket, buzzing every 15 minutes. He is the notification on your phone saying \"Bitcoin is down 10%\" or \"Nvidia hits all-time high.\" The modern Mr. Market is amplified by algorithms designed to trigger your dopamine and cortisol.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Consider the \"Flash Crash\" or the meme-stock frenzy. These are Mr. Market on steroids—millions of people reacting to a viral sentiment rather than business fundamentals. If you own an S&P 500 ETF, you own the 500 biggest companies in America. Did the value of Coca-Cola or Microsoft actually drop 20% in a week because of a geopolitical rumor? No. Their factories didn't burn down. Their customers didn't vanish. Only the price tag changed. If you react to the notification, you are letting the algorithm trade against you. You must view volatility not as risk, but as the fee you pay for higher returns.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "Set Your X-Hour Rule and Write It Down",
                                description: "If you feel the urge to change your portfolio based on a news headline or a price drop, you must wait X hours. No exceptions. Panic is a chemical reaction; it fades.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 208,
                currentProgress: 0.0
            ),
            4: CoreChapterContent(
                chapterNumber: 4,
                chapterTitle: "Building the Fortress (The Defensive Strategy)",
                bookTitle: "The Intelligent Investor",
                bookAuthor: "Benjamin Graham",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Myth of the Mastermind")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("There is a persistent lie in finance that \"effort equals return.\" In your job, if you work 80 hours a week, you get a promotion. In the stock market, if you work 80 hours a week analyzing charts, reading news, and tweaking your portfolio, you will likely underperform a dead person. The friction here is ego. You believe you are smarter than the market. You believe you can time the top and the bottom. You believe that \"doing nothing\" is lazy. But in investing, activity is the enemy of returns. The more you touch your portfolio, the more you incur fees, taxes, and emotional errors. The goal of the Fortress is to protect you from your own desire to be clever.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The 50/50 Split")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author solves this by creating a specific category for people who want to get rich without making investing their second job: the \"Defensive Investor.\" He proposes a radical simplicity. Imagine a portfolio that is a perfect balance scale. On one side, you have high-grade bonds (lending money to stable governments or corporations). On the other side, you have a diversified list of leading common stocks (owning a piece of the economy).")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author suggests a baseline split of 50% stocks and 50% bonds. This is your \"Fortress.\" When the stock market crashes and people are jumping out of windows, your bonds hold their value, and your portfolio only drops half as much as the market. You sleep well. When the market booms and your neighbor is bragging about his gains, your stocks participate in the rally. You never fully lose, and you never miss out. The genius is in the maintenance: if stocks go up and become 60% of your portfolio, you simply sell some stocks and buy bonds to get back to 50/50. This forces you to do the one thing everyone fails at: sell high and buy low, automatically.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Autopilot Advantage")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, building this Fortress is easier than the author could have ever dreamed. In his day, you had to buy individual bonds and pick 20 different stocks. Today, you can build the entire Fortress with two clicks using low-cost Index Funds or ETFs (Exchange Traded Funds).")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Instead of picking \"winners\" (which is hard), you buy the entire S&P 500 or a Total World Stock ETF. You own Apple, Microsoft, Amazon, and 4,000 other companies. If one goes bankrupt, you don't even notice. If a new AI giant emerges, you automatically own it. This is the \"Boglehead\" philosophy (named after Jack Bogle, who popularized index funds), which is the modern spiritual successor to Graham’s Defensive strategy. It accepts that you cannot predict the future, so you simply own the entire economy. It turns investing from a stress-inducing hunt for \"the next Bitcoin\" into a boring, reliable utility bill that pays you.")
                    ),
                ],
                audioDurationSeconds: 193,
                currentProgress: 0.0
            ),
            5: CoreChapterContent(
                chapterNumber: 5,
                chapterTitle: "Here is the content for Core 5, where we outline the strict rules for those who want to beat the market.",
                bookTitle: "The Intelligent Investor",
                bookAuthor: "Benjamin Graham",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("Core 5: Going on the Offensive (The Enterprising Strategy)")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Hero Complex")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is the seduction: You believe that if you are smarter, faster, and read more news than your neighbor, you will make more money. You view the stock market as a test of your intelligence. This is the \"Hero Complex,\" and it is the fastest way to underperform. The friction is simple: you are not competing against your neighbor. You are competing against institutional algorithms that read every news headline in milliseconds and price it in before you’ve even unlocked your phone. If you try to \"win\" by buying the same popular, high-growth companies everyone else loves, you are paying a premium for a consensus view. You are buying a Ferrari at full sticker price and hoping to sell it for a profit. That isn't investing; that's wishful thinking.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Negative Art")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author solves this by redefining what it means to be \"Enterprising.\" He clarifies that being an Enterprising Investor is not about taking more risk; it is about doing more work. It is a job description, not a personality type.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author warns against the siren song of \"Growth Stocks\"—the companies that everyone knows are the future. He tells the story of the investor who buys the great companies of the era (like the radio or airline pioneers) and loses everything because the price was already bid up to perfection. Instead, the Enterprising Investor practices a \"negative art.\" They avoid the popular. They hunt in the scrap heap. They look for \"bargain issues\"—stocks selling for less than their working capital. They buy when the news is bad, the outlook is gloomy, and the crowd is selling. The Enterprising Investor makes their money not by predicting the future, but by correcting the market's mistakes in the present.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Edge of Discomfort")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the modern era, the \"easy\" information is gone. You cannot find a bargain just by looking at a P/E ratio on Yahoo Finance because a million other people have seen it too. Today, the \"Enterprising\" edge is almost entirely psychological. It is the ability to endure social pain.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Think about the collapse of Meta (Facebook) in 2022 or the energy sector when oil prices went negative in 2020. The \"Enterprising\" move wasn't to buy Nvidia when it was on every magazine cover; it was to buy the unloved, \"boring,\" or \"dead\" sectors when everyone else was chasing AI hype. If you are buying a stock that makes you feel smart at a dinner party, you are likely overpaying. The modern Enterprising strategy often involves \"Deep Value\" or \"Special Situations\"—spinoffs, distressed assets, or small-cap companies too small for the big funds to touch. You are looking for the \"ick factor\"—assets that are fundamentally sound but temporarily hated.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Protocol")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The Dinner Party Test",
                                description: "If you mention a stock you own and everyone nods in agreement, put it on your \"Watch List\" to potentially exit. If you mention it and they grimace or ask \"Why?\", you are likely on the right track.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 210,
                currentProgress: 0.0
            ),
            6: CoreChapterContent(
                chapterNumber: 6,
                chapterTitle: "The Mutual Fund Maze",
                bookTitle: "The Intelligent Investor",
                bookAuthor: "Benjamin Graham",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Croupier's Cut")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The financial industry is built on a single, powerful myth: \"Investing is too complicated for you to do alone; you need an expert.\" So, you hand your money to a mutual fund manager—a \"star\" in a suit who promises to navigate the market's storms. You pay them a 1% or 2% fee every year, regardless of whether they make you money or lose it. The friction here is mathematical, not emotional. That 2% fee sounds small, but over 30 years, it can consume up to 40% of your total wealth. You are taking 100% of the risk, providing 100% of the capital, and giving away nearly half the upside to a manager who, statistically, will fail to beat the market anyway. You aren't hiring a guide; you're carrying a parasite.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Rearview Mirror Trap")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author saw this coming decades ago. He identified the fatal flaw in how mutual funds are sold: Performance Chasing. He describes the \"Hot Hand\" fallacy—the investor who looks at a list of funds, picks the one that went up 50% last year, and assumes it will do the same next year.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Graham warns that \"past performance is not a guarantee of future results\" isn't just a legal disclaimer; it is an iron law. He observed that funds with spectacular returns usually achieved them by taking spectacular risks or getting lucky in a specific sector. Once that sector cools off, the \"genius\" manager looks like a fool, and the investor who bought in at the top suffers the crash. The author’s solution was brutal honesty: most funds are just expensive marketing machines designed to gather assets, not to generate alpha.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Index Revolution")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, the trap is even more subtle. We have \"Thematic ETFs\"—funds dedicated to AI, Cannabis, or Space Travel. These are the modern version of the \"hot\" mutual funds Graham warned about. They package a compelling narrative (e.g., \"Robots are the future!\") and charge you a premium to own a basket of hype.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The \"Wiser\" analysis, championed by Graham’s disciple John Bogle, is that you should stop looking for the needle in the haystack and just buy the haystack. The modern S&P 500 Index Fund allows you to fire the manager. Instead of paying 1.5% for a human to guess which stocks will go up, you pay 0.03% to a computer to own all the stocks. In a world where information is instant, the \"edge\" that active managers used to have is gone. By indexing, you guarantee you will outperform 90% of professional investors simply by refusing to pay their salaries.")
                    ),
                ],
                audioDurationSeconds: 174,
                currentProgress: 0.0
            ),
            7: CoreChapterContent(
                chapterNumber: 7,
                chapterTitle: "The Earnings Mirage",
                bookTitle: "The Intelligent Investor",
                bookAuthor: "Benjamin Graham",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The greatest lie on Wall Street is a single number: \"EPS\" (Earnings Per Share). The friction is that you are trained to treat this number as a hard fact. When a company \"beats earnings estimates,\" the stock pops, and you feel safe. But in reality, \"Net Income\" is often an opinion—a flexible figure massaged by accountants to make management look good. Cash, on the other hand, is a fact. Most investors fail because they buy a company based on the headline profit number without realizing the business is actually bleeding cash to keep the lights on. They are buying a beautifully painted car with no engine.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Kitchen Sink Trap")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author exposes this by taking us into the dark room of corporate accounting. He tells the story of the \"Big Bath\"—a trick where a new CEO arrives and immediately writes off every possible bad investment, lawsuit, and mistake in a single quarter. This makes the company look terrible for three months (which the CEO blames on the previous guy), but it clears the decks so that future earnings look artificially explosive.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Graham warns us to ignore \"Pro Forma\" earnings—earnings that exclude \"bad stuff.\" He likens it to a man saying he saved $1,000 this month, provided you ignore the $5,000 he spent on gambling because that was a \"one-time event.\" The author’s solution is to distrust the Income Statement and focus on the Balance Sheet and the dividend record. If a company says it is making millions but isn't paying dividends or building up cash, the author asks: \"Where is the money?\"")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The EBITDA Alibi")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the modern era, this manipulation has evolved into \"Adjusted EBITDA\" (Earnings Before Interest, Taxes, Depreciation, and Amortization). This is a metric often used by high-growth Tech and AI companies to pretend they are profitable when they are not.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Consider the WeWork debacle, where the company used \"Community Adjusted EBITDA\" to hide massive losses. Or look at modern software companies that pay their employees in millions of dollars of Stock-Based Compensation (SBC). They claim to be \"profitable\" on an adjusted basis because they treat that stock issuance as free money. But it isn't free—it dilutes you. It slices the pizza into more pieces, making your slice smaller. If you only look at the \"Non-GAAP\" earnings promoted in the press release, you are being legally lied to.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Protocol")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The Dilution Check",
                                description: "Open Yahoo Finance website, in the income statement, look at the \"Weighted Average Shares Outstanding\" over the last 3 years. If the number of shares is going up every year, management is funding the company by printing stock—slowly stealing the company from you.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 177,
                currentProgress: 0.0
            ),
            8: CoreChapterContent(
                chapterNumber: 8,
                chapterTitle: "The Comparison Test",
                bookTitle: "The Intelligent Investor",
                bookAuthor: "Benjamin Graham",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Vacuum Trap")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The biggest mistake you make is falling in love with a stock in isolation. You see a company—let's say a popular coffee chain—and you think, \"I love their coffee, the line is always out the door, this must be a great investment.\" You buy it because the story feels good. This is the \"Vacuum Trap.\" You are judging an asset without a benchmark. It is like buying a house for $1 million without checking if the identical house next door just sold for $500,000. Friction arises because you are judging the quality of the product rather than the value of the business. A great company can be a terrible investment if you pay the wrong price.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Tale of Two Companies")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author shatters this illusion by playing a game of \"Spot the Difference.\" He takes two companies in the same industry—often a popular \"glamour\" stock and an unloved \"boring\" stock—and places their financials side-by-side, stripping away their names.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He tells the story of Company A (the darling) which trades at 40 times its earnings, and Company B (the outcast) which trades at 10 times its earnings. Company A has a great story, but its balance sheet is loaded with debt. Company B is boring, but it has zero debt and twice the cash reserves. The author reveals that to justify its price, Company A would have to grow at a miraculous rate for a decade, while Company B just has to stay alive to make you money. By forcing a head-to-head comparison, the \"obvious\" choice often flips. The glamour stock is revealed as a speculation; the boring stock is revealed as the investment.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"Tesla\" Premium")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the modern era, this disparity has exploded. We see it in the \"Magnificent Seven\" versus the rest of the market. You might look at a high-flying EV maker or an AI chip designer trading at 80x earnings and think it's the only game in town. But if you run the \"Comparison Test\" against a legacy competitor—say, a boring semiconductor firm or an established auto manufacturer—you often find that the \"old\" company has higher profit margins, better dividends, and a valuation that is 80% cheaper.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The modern market pays a massive premium for \"narrative.\" It overvalues the disruptor and undervalues the incumbent. The Wiser Analysis isn't that you should never buy the growth stock; it’s that you must quantify exactly how much \"optimism\" you are paying for. If you are paying 5x more for a dollar of earnings from Company X than from Company Y, you better be certain Company X is five times better. Usually, it isn't.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Protocol")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Rule of Three\"",
                                description: "Never analyze a stock alone. Before you make a decision, you must line it up against its two largest competitors.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The Blind Test",
                                description: "Create a simple spreadsheet with three columns: P/E Ratio, Debt-to-Equity, and Dividend Yield. Hide the names. Which set of numbers would you buy if you didn't know the brand? If it’s not the one you wanted to buy, you are buying hype, not value.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 206,
                currentProgress: 0.0
            ),
            9: CoreChapterContent(
                chapterNumber: 9,
                chapterTitle: "Filtering for Quality (The Defensive Screen)",
                bookTitle: "The Intelligent Investor",
                bookAuthor: "Benjamin Graham",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Junk Food Portfolio")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Most investors suffer from financial obesity. You fill your portfolio with \"empty calories\"—companies that have great stories but zero nutritional value. You buy the hot EV startup that hasn't sold a car, or the biotech firm with no revenue, because you are hungry for growth. The friction is that in a bull market, junk rises just like quality. You feel smart holding these fragile companies. But when the market turns, the junk crashes 90% and never recovers, while the quality stocks just go on a diet. You fail because you have no standards; you let any stock into your house just because it knocked on the door.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Seven-Point Bouncer")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author solves this by hiring a ruthless bouncer for your portfolio. He creates a specific \"Defensive Screen\"—a checklist of seven strict requirements that a company must meet before you even consider buying it.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author tells us that a Defensive Investor is not a talent scout looking for the next big thing; he is a quality control inspector. He demands:")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Adequate Size: No small, risky companies.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Strong Financials: Current assets must be at least twice current liabilities.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Earnings Stability: Positive earnings for the last 10 years. No losses allowed.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Dividend Record: Uninterrupted payments for 20 years.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Earnings Growth: A minimum increase of 33% over the last 10 years.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Moderate P/E: The price should not be more than 15 times average earnings.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Moderate Price-to-Book: The price should not be more than 1.5 times the book value.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("If a stock fails even one of these tests, the bouncer throws it out. It doesn't matter if it's the \"future of technology\"; if it doesn't have the numbers, it doesn't get in.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Zombie Apocalypse")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the modern era, this screen is your only defense against \"Zombie Companies\"—firms that don't earn enough profit to cover the interest on their debt. In the last decade of near-zero interest rates, thousands of these zombies have survived by borrowing cheap money. They are the SPACs, the unprofitable tech IPOs, and the \"disruptors\" that bleed cash.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("You can apply Graham’s screen to filter out the noise of the modern tech bubble. While you might relax the P/E ratio slightly for high-quality tech giants (who have asset-light models), the core principle remains: Profits matter. Dividends matter. A company like Uber or Airbnb in their early years would have been rejected by this screen—and Graham would be fine with that. He would rather miss a Google than suffer a WeWork. Today, you can automate this by looking at \"Dividend Aristocrats\" or \"Quality Factor\" ETFs, which effectively run this code for you.")
                    ),
                ],
                audioDurationSeconds: 178,
                currentProgress: 0.0
            ),
            10: CoreChapterContent(
                chapterNumber: 10,
                chapterTitle: "Hunting for Bargains (The Enterprising Screen)",
                bookTitle: "The Intelligent Investor",
                bookAuthor: "Benjamin Graham",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Dumpster Diver's Dilemma")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("To be an Enterprising Investor, you must be willing to do something that feels physically repulsive: you must buy what others are throwing away. The friction here is intense social pressure. When a company is failing, the headlines are screaming \"Bankruptcy!\" and your friends are laughing at it. To buy it then requires a stomach of steel. Most people fail at this because they crave the safety of the herd. They want to buy \"good\" companies that are winning. But the Enterprising Investor knows a secret: a \"bad\" company at a dirt-cheap price is a better investment than a \"good\" company at an exorbitant price. You aren't looking for quality; you are looking for a mispricing so severe that the market is practically handing you free money.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Cigar Butt Strategy")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The author famously formalized this with the concept of the \"Net-Net.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He tells the story of a specific type of bargain that was once common: a company trading for less than its liquidation value. Imagine a company that has $10 million in cash and inventory, owes $2 million in debt, and has a market cap of only $5 million. Even if the business shuts down tomorrow, sells the inventory, and pays off the debt, there is $8 million left. If you buy the whole company for $5 million, you make an instant $3 million profit.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("This is the \"Cigar Butt\" approach. You find a discarded cigar on the street that is soggy and gross, but it has one good puff left in it. It’s free, so that puff is pure profit. The author’s solution for the Enterprising Investor is to hunt for these statistical anomalies—companies worth more dead than alive—and buy a basket of them. You don't care about their future; you care that you bought a dollar bill for 50 cents.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Hidden Assets of the Digital Age")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the modern market, true \"Net-Nets\" are rare because computers find them instantly. However, the principle remains the most powerful tool in deep value investing. Today, the \"hidden assets\" aren't factories or piles of coal; they are intellectual property, user bases, or even Bitcoin treasuries.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Consider a legacy tech company that everyone thinks is a dinosaur. The stock is crushed. But on its balance sheet, it owns a massive portfolio of patents or a stake in a hot AI startup that the market has forgotten about. Or consider a retail chain that owns its own real estate. The market values the business at zero because \"retail is dead,\" but the land under the stores is worth more than the stock price. This is the modern \"sum-of-the-parts\" trade. You are looking for \"Special Situations\" where the market is so focused on the dying business that it ignores the valuable assets locked inside.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Protocol")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Bad News\" Filter",
                                description: "Specifically search for sectors that are currently hated (e.g., \"commercial real estate\" or \"regional banks\"). Look at the 52-week low price. If a stock hasn't dropped 50% in the last year, it probably isn't cheap enough for this strategy.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 207,
                currentProgress: 0.0
            ),
            11: CoreChapterContent(
                chapterNumber: 11,
                chapterTitle: "Spotting the Red Flags",
                bookTitle: "The Intelligent Investor",
                bookAuthor: "Benjamin Graham",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Optimism Blindfold")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The most dangerous defect in the human brain is the ability to ignore data that contradicts a happy story. You buy a stock because the CEO is charismatic and the vision is \"world-changing.\" When the footnotes in the financial report show massive debt or insider selling, you gloss over them. \"They're just investing for growth,\" you tell yourself. The friction is that you are analyzing the narrative, not the structure. You are admiring the paint job on a house while termites are eating the foundation. By the time the floor collapses, it is too late.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Conglomerate House of Cards")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author masterfully exposes this by taking us back to the \"Conglomerate Boom.\" He tells the story of companies that stopped inventing products and started inventing accounting tricks.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Imagine a boring company that decides to become \"exciting\" by buying up random businesses—a steel mill, an airline, a rental car agency. On paper, their revenue explodes. The CEO looks like a genius. But the author peels back the curtain to reveal the rot: they paid for these acquisitions with overpriced stock and massive debt. The \"growth\" was an illusion created by merging sloppy businesses together. The \"Synergy\" they promised never happened. When the economy slowed down, the debt remained, but the cash flow vanished. The stock, once a darling of Wall Street, went to zero. The lesson? Complexity is often a mask for fraud. If you cannot understand how a company makes money in two sentences, it probably doesn't.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"Community Adjusted\" Reality")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, the red flags are hidden in plain sight under the guise of \"Innovation.\" The modern equivalent of the Conglomerate trick is the \"Pivot.\" When a crypto exchange collapses, or an \"AI\" startup implodes, the signs were always there.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Consider the recent \"SPAC\" (Special Purpose Acquisition Company) mania. Companies with zero revenue were taken public based on PowerPoint slides projecting trillions in future profit. Or look at the \"WeWork\" disaster, where the company invented a metric called \"Community Adjusted EBITDA\" to pretend that renting desks at a loss was actually profitable. They removed the \"bad\" costs (like rent and marketing) to show a \"good\" number. This is not accounting; it is fiction. If a company pivots its entire business model to the latest buzzword (like adding \"Blockchain\" in 2017 or \"AI\" in 2024) without a working product, run. They are selling you stock, not software.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Protocol")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The Insider Exit",
                                description: "If the CEO and CFO are \"consistently\" selling their own stock while telling you to buy it on CNBC, believe their actions, not their words.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 174,
                currentProgress: 0.0
            ),
            12: CoreChapterContent(
                chapterNumber: 12,
                chapterTitle: "The Golden Rule (Margin of Safety)",
                bookTitle: "The Intelligent Investor",
                bookAuthor: "Benjamin Graham",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Perfectionist's Fallacy")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The biggest lie you tell yourself is that you can predict the future. You build complex spreadsheets projecting revenue for the next ten years. You tell yourself, \"If they grow at 15% and margins stay at 20%, the stock is worth exactly $142.50.\" The friction here is arrogance. The world is chaotic. Pandemics happen. Wars start. CEOs get fired. If your investment only works when everything goes perfectly, you are not investing; you are walking a tightrope without a net. One gust of wind, and you are dead. Most people fail because they pay a price that demands perfection.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Engineering Secret")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author boils down the secret of investment success into three words: Margin of Safety. To explain it, he leaves the world of finance and enters the world of engineering.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He tells the story of building a bridge. If you need a bridge to support a 10,000-pound truck, do you build it to hold exactly 10,000 pounds? No. If the truck is slightly heavier or the steel is slightly weaker, the bridge collapses. You build it to hold 30,000 pounds. That extra 20,000 pounds of capacity is your \"Margin of Safety.\" It is the buffer that allows for rust, bad weather, and human error.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In investing, this means if you calculate a business is worth $100 a share (its \"intrinsic value\"), you do not pay $100. You pay $60. That $40 gap is your protection. It means the company can have a bad year, the economy can tank, or your math can be wrong, and you still won't lose money. If things go right, you make a fortune. If things go wrong, you break even. You win by refusing to pay full price for value.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Buffer Against Chaos")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the modern world, this concept is the only thing standing between you and total ruin. We live in an era of \"Black Swans\"—unpredictable events like COVID-19 or the 2008 Financial Crisis that shatter standard economic models.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Consider the investors who bought \"safe\" tech stocks at 100 times earnings in 2021 because \"rates will stay low forever.\" They had zero Margin of Safety. When rates rose, their portfolios fell 70%. Contrast this with the investor who bought energy stocks when oil was negative in 2020. They bought assets for pennies on the dollar. Even if the transition to green energy happens faster than expected, they bought so cheaply that they can't lose. In a world of algorithms and AI trading, the Margin of Safety is your humility. It is the admission that you don't know what will happen next, so you demand a discount to compensate for your ignorance.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Protocol")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Two-Thirds\" Rule",
                                description: "Never pay more than 2/3rds of your calculated value for a company. If you think it's worth $30, and it's trading at $29, walk away. Wait for $20.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The Cash Bunker",
                                description: "Keep 10-20% of your portfolio in short-term government bonds or cash equivalents. This is not for \"safety\"; this is your ammunition. When the market crashes and creates a massive Margin of Safety in great companies, you need dry powder to deploy.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 211,
                currentProgress: 0.0
            ),
        ],
        3: [
            1: CoreChapterContent(
                chapterNumber: 1,
                chapterTitle: "The Spreadsheet Delusion",
                bookTitle: "The Psychology of Money",
                bookAuthor: "Morgan Housel",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Have you ever noticed how the smartest people in the room often make the most disastrous financial decisions? The friction comes from a fundamental misunderstanding: we treat finance like it is physics. We believe it operates on immutable laws, formulas, and perfectly rational answers. We assume that if we just build a complex enough system—perhaps engineering intricate data architecture to process market trends and chart the future—we can perfectly conquer the market.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But human beings aren't code. We fail because we optimize for a spreadsheet instead of optimizing for a peaceful night's sleep. When you design a flawless financial plan on a quiet Sunday afternoon, you are acting rationally. When the market drops 15% on a Tuesday morning and your heart starts pounding, rationality evaporates. The perfectly calculated plan shatters against the reality of human emotion. The secret the industry tries to hide is that trying to be completely, coldly rational is actually a trap.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Fever of the Era")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author tells a compelling story about how our personal history dictates our financial reality. Imagine two different people. One was born in 1950 and watched the stock market go essentially nowhere during their formative years in the 1970s. The other was born in 1970 and came of age during the unstoppable roaring bull market of the 1990s.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("If you ask both of them about risk, you will get two entirely opposite answers. And the fascinating part? No one is crazy. The author points out that every financial decision makes perfect sense to the person making it at that exact moment, heavily filtered through the tiny sliver of economic history they personally experienced. We are all viewing money through a highly distorted, individualized lens.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Because our lenses are so biased, the author argues that aiming for strict rationality is a vulnerability. A purely rational investor might look at historical data and decide to leverage themselves to the absolute limit because the expected return is mathematically positive. But a reasonable investor knows that if a sudden dip causes a margin call, or simply triggers enough anxiety to force a panic exit, the math is entirely useless. You must aim for a strategy that is reasonable enough to let you stay in the game long-term, even if it isn't flawlessly rational on paper.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("Trading the Algorithm for the Human")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Let’s drag this into the reality of right now. We are living in an era of overwhelming technological acceleration. AI infrastructure is booming, data centers are eating massive amounts of capital, and markets swing wildly based on geopolitical murmurs or the sudden movement of a single crypto whale.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, you hold an unprecedented amount of analytical power right in your pocket. You can deploy an autonomous Deep Research Agent to parse a decade of dense 10-K filings in minutes, and it might tell you exactly what you need to know about a company. The AI feels no fear. It doesn't sweat. It only sees pure expected value and cold, hard rationality. But you are the one who has to stare at the screen when a sudden supply chain shock causes those exact companies to plummet 20% in a week. When you look at your phone and see those red numbers flashing across your live widget, the psychological pain immediately overrides the AI's logical blueprint.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In today's world of frictionless, hyper-fast mobile finance, the ultimate edge isn't just having the most sophisticated data or the fastest alert system. It is building a strategy that actively accommodates your own human flaws. If maintaining a theoretically \"perfect\" portfolio keeps you staring at the ceiling at 2 AM, it is a catastrophic strategy for you. Wealth isn't built by the person with the smartest data; it is built by the person who creates a framework reasonable enough to survive their own worst impulses.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "Audit Your Sleep Factor",
                                description: "Open your Tracking tab and review your Watchlist. Look at those assets and ask yourself a ruthless question: \"If the market drops 20% tomorrow, will staring at these red numbers cause me to panic?\" Adjust your exposure until you can sleep.Adjust your exposure until the answer is a definitive \"no.\"",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 279,
                currentProgress: 0.0
            ),
            2: CoreChapterContent(
                chapterNumber: 2,
                chapterTitle: "The Illusion of Complete Control",
                bookTitle: "The Psychology of Money",
                bookAuthor: "Morgan Housel",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Listen closely. The financial world is obsessed with the myth of the self-made visionary. When we see someone accumulate massive wealth, our immediate instinct is to dissect their morning routine, their work ethic, and their precise execution. We assume their success is a direct, one-to-one output of their intelligence. Conversely, when we fail, we beat ourselves up, assuming we just weren't smart enough or didn't grind hard enough.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("This is the friction that breaks most investors. We completely ignore the massive, invisible forces constantly acting upon our lives and our portfolios. We look at the winners and try to perfectly replicate their exact steps, completely forgetting that it is impossible to replicate their exact circumstances. When you believe you are in complete control of the outcome, every loss destroys your confidence, and every win inflates your ego to dangerous levels.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Tragic Flip of the Coin")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author illustrates this profound truth with the story of Bill Gates and his high school friend, Kent Evans. In 1968, Bill Gates attended Lakeside School, which happened to be one of the only high schools in the entire world with access to a computer. The odds of a teenager having access to that kind of computing power at that time were roughly one in a million. That extraordinary stroke of luck set the stage for the creation of Microsoft.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But there is a darker half to this story. Gates had a best friend named Kent Evans. Kent was just as brilliant as Gates, just as obsessed with coding, and shared the exact same world-changing ambition. But before they even graduated, Kent died in a tragic mountaineering accident. The odds of a high school student dying on a mountain are also about one in a million.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Gates experienced a one-in-a-million stroke of positive variance. That is luck. Kent experienced a one-in-a-million stroke of negative variance. That is risk.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Luck and risk are siblings; they are doppelgängers. The author reveals that outcomes are rarely just the result of individual effort. Furthermore, the vast majority of massive financial success is driven by \"tails\"—extreme, rare outlier events.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In finance, as in business, you can be completely wrong half the time and still make a fortune, provided your few successful ventures catch a massive tailwind. You don't need to be right every time. You just need to survive long enough to catch the right wave.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("Catching the Algorithmic Tailwind")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Let's drag this into the reality of the modern market. Think about the explosive rise of AI and data center infrastructure over the last few years. Someone who heavily allocated capital into specific GPU manufacturers five years ago might be parading around as a visionary genius today. But were they truly predicting the exact trajectory of generative AI, or did they happen to be standing in the right sector right before a massive, unpredictable technological boom reshaped the global economy?")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("If you try to flawlessly replicate their specific strategy now, assuming it was pure skill, you might end up swallowing the \"risk\" side of the coin just as the market cycle shifts. In today’s hyper-connected environment—where a single regulatory shift in Clean Energy, a sudden breakthrough in decentralized networks, or an overnight geopolitical trade war can erase billions in value—you cannot accurately predict the future. The variables are too dense.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Your objective is not to be a flawless oracle. Your objective is endurance. You must construct a system robust enough to weather the 99 terrible or mediocre days so that your capital is still sitting at the table when that 1 extraordinary day of massive returns finally arrives. The secret isn't predicting the tails; it's surviving the wait.")
                    ),
                ],
                audioDurationSeconds: 246,
                currentProgress: 0.0
            ),
            3: CoreChapterContent(
                chapterNumber: 3,
                chapterTitle: "The Moving Goalpost Syndrome",
                bookTitle: "The Psychology of Money",
                bookAuthor: "Morgan Housel",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Have you ever noticed that hitting your financial targets rarely feels as good as you thought it would? You hustle, you scrape, you finally hit that magic number in your portfolio—and within forty-eight hours, the satisfaction evaporates. You immediately set a new, higher target. The friction here is that most people believe wealth is a numbers game, but it is actually a psychology game. We fail at this because we allow our ego to scale linearly with our net worth. We step onto a hedonic treadmill, convinced that if we just get a little bit more, we will finally arrive. But the goalpost has wheels. People destroy perfectly good financial plans, taking catastrophic risks, simply because they are playing a game of comparison they can never win.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Phantom in the Ferrari")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author crystalizes this trap through a brilliant concept called the \"Man in the Car Paradox.\" Imagine you are walking down the street and a sleek, roaring $200,000 Ferrari pulls up to the stoplight.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("What goes through your mind? You almost never look at the driver and think, \"Wow, the guy driving that car is incredibly cool and successful. I admire him.\" Instead, you look at the car and think, \"If I had that car, people would think I am cool.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Do you see the tragic irony? The driver purchased the Ferrari explicitly to signal their wealth and command your admiration. But you bypass the driver entirely, using their expensive possession as a mirror to reflect your own desires for status. Nobody is as impressed with your stuff as you are. The author also notes the tragic trajectory of incredibly wealthy executives—people who already had hundreds of millions of dollars—who committed financial crimes just to get a little bit more. They risked something they had and needed (their freedom and reputation) for something they didn't have and didn't need (a higher ranking on a billionaire's list). They completely forgot how to define the word \"enough.\"")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("Flexing in the Digital Colosseum")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Let's pull this into the hyper-connected reality of today. The \"Man in the Car\" is no longer just a guy at a stoplight; he is everywhere. The ego trap has gone digital, frictionless, and infinite.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Fifteen years ago, you only had to compare your financial success to your immediate neighbors. Today, because of social media, remote work digital nomads, and viral trading communities, you are forced to compare your portfolio to the top 0.001% of the global internet. You might execute a brilliant, disciplined strategy, securing a solid, sustainable return on a clean energy ETF or a well-researched AI infrastructure play. You should be thrilled. But then you open your phone and see a 19-year-old who turned a stimulus check into millions by gambling on a decentralized crypto token.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Instantly, your very reasonable success feels like a failure. Your ego screams that you are falling behind. In the modern market, the ultimate scarcity isn't capital or data; it is the psychological armor required to ignore what everyone else is doing. If you let the viral screenshots of overnight tech millionaires dictate your baseline for success, you will eventually take uncharacteristic risks that blow up your entire life's work. You have to realize that comparing your behind-the-scenes reality to someone else's curated highlight reel is a rigged game. The only way to win is to refuse to play.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "Establish Your \"Enough\"",
                                description: "Navigate to the Wiser tab, use the AI chat to document what \"enough\" looks like for your actual life—not your internet persona. Let Caudex AI help you visualize your future and write down the specific lifestyle you are trying to fund. Once your system is engineered to hit that specific target, the noise of other people's money becomes entirely irrelevant.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 252,
                currentProgress: 0.0
            ),
            4: CoreChapterContent(
                chapterNumber: 4,
                chapterTitle: "Ignite the Compounding Engine",
                bookTitle: "The Psychology of Money",
                bookAuthor: "Morgan Housel",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Curse of the Maximum Yield")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is a secret that the loudest voices in the financial media desperately want you to ignore: hunting for the highest possible return is usually what destroys your wealth. The friction here is rooted in our impatience. We treat investing like a drag race, constantly scanning the horizon for the fastest-moving vehicle. If one asset is up 20% and ours is only up 8%, we feel like we are losing. So, we switch lanes. We pivot. We interrupt the process.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But wealth creation isn't a drag race; it is a glacier. Most people fail because they misunderstand the fundamental math of compounding. They think compounding is about earning massive percentages every single year. It isn't. Compounding is a stubborn, slow-moving machine that only requires one input to work its magic: unbroken, continuous time. Every time you panic, chase a new trend, or try to dodge a market dip, you unplug the machine and reset the clock to zero.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The $81 Billion Birthday and the Price of Admission")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author shatters this illusion by pointing to the most famous investor alive: Warren Buffett. We attribute Buffett’s fortune to supreme stock-picking intelligence. But the author reveals a staggering mathematical reality: of Buffett's roughly $84.5 billion net worth (at the time the book was written), $81.5 billion of it came after his 65th birthday.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Buffett isn't the greatest investor of all time because he had the highest annual returns. In fact, there are quantitative fund managers who boast significantly higher average annual returns than him. Buffett's secret is simply that he has been achieving good returns uninterrupted for three-quarters of a century. He never stopped the engine.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But how do you keep the engine running when the market collapses? The author explains this through the metaphor of a \"fee versus a fine.\" When you get a speeding ticket, that is a fine. It means you did something wrong; you are being punished and you should change your behavior. But when you pay $150 to get into Disneyland, that is a fee. It is simply the price of admission to experience something great. Market volatility—the agonizing 20% or 30% drops—is not a fine. It does not mean you made a mistake. It is the unavoidable fee you must pay to access the theme park of long-term compounding.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("Weathering the AI Super-Cycle")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Let’s contextualize this for the modern market. We are currently watching a massive structural shift regarding artificial intelligence, autonomous networks, and global data center expansion. The long-term trajectory of these technologies is incredibly profound.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("However, the day-to-day reality of participating in this super-cycle is brutal. A sudden semiconductor export restriction, a minor trade war whisper, or an unexpected shift in remote-work enterprise spending can send a perfectly healthy AI infrastructure company plummeting 35% in a single month.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("When modern investors see that flash crash on their screens, their instinct is to view it as a speeding ticket. They think, “I made a mistake, I need to exit immediately to stop the pain.” But if you truly understand the underlying thesis of the asset, that 35% drop is just the admission fee. If you refuse to pay the fee of modern market volatility, you will never be allowed to stay on the ride long enough to let the compounding engine do its actual work. You cannot get a multi-decade technological tailwind for free.")
                    ),
                ],
                audioDurationSeconds: 228,
                currentProgress: 0.0
            ),
            5: CoreChapterContent(
                chapterNumber: 5,
                chapterTitle: "The Schizophrenia of Wealth",
                bookTitle: "The Psychology of Money",
                bookAuthor: "Morgan Housel",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Getting wealthy is glamorous; staying wealthy is agonizingly dull. The financial world glorifies the ascent. We obsess over the visionaries who see what no one else sees, the risk-takers who put everything on the line, and the bold optimists who look at a skeptical market and confidently declare, \"I am right, and everyone else is wrong.\" Getting money absolutely demands this aggressive, forward-leaning posture. You must be optimistic enough to believe that the future will be larger and more profitable than the past.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But here is the brutal friction that shatters most portfolios: the very psychological traits required to make money are the exact traits that will mathematically guarantee you lose it. People fail because they treat wealth generation and wealth preservation as the exact same discipline. They are not just different skills; they are polar opposites. To keep your wealth, you must undergo a complete psychological inversion. You have to trade your visionary optimism for a creeping, constant paranoia. You must assume that your past success was largely a byproduct of a temporary tailwind, and that tomorrow's market is actively conspiring to take it all back. When you refuse to shift gears—when you keep your foot slammed on the accelerator of optimism after you have already won the race—you eventually drive your portfolio straight off a cliff.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Ghost of Jesse Livermore and the Mechanics of Survival")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author illustrates this catastrophic failure to shift gears through the haunting story of Jesse Livermore. In the early 20th century, Livermore was arguably the greatest stock trader alive. When the catastrophic market crash of 1929 hit, wiping out a generation of wealth and plunging the nation into the Great Depression, Livermore’s wife locked their doors, assuming they were completely ruined like everyone else in their social circle. But Livermore returned home with unbelievable news: he had aggressively shorted the market. While the world burned, Livermore made the modern equivalent of over three billion dollars in a single week.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He had achieved the ultimate financial victory. He was invincible. But Livermore could not turn off the risk-taking engine that made him a billionaire. He kept placing massive, highly leveraged, wildly optimistic bets on the market, assuming his own genius was a permanent shield. He lacked the paranoia required to protect what he had built. Four years later, Livermore had lost absolutely everything, culminating in tragedy.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("This story perfectly highlights the author’s concept of \"Room for Error,\" often called the margin of safety. Room for error is the unspoken, unglamorous secret of the world's most enduring investors. It is the understanding that your predictive models will eventually be wrong. If your financial plan requires the market to perform exactly as you predicted in order for you to survive, you are playing Russian roulette. Building an unbreakable defense means engineering a life and a portfolio that can endure a reality where your assumptions are completely shattered. It is the realization that survival is not just one of the strategies; survival is the only foundation upon which compounding can actually work. If you are forced out of the game during a downturn, your past brilliance is entirely erased.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("Surviving the Algorithmic Meat Grinder")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Fast forward to our current reality. The velocity of money and the sheer speed of information have violently accelerated. We are navigating an era of unprecedented leverage and hyperspeed narrative-driven markets. Consider the billions of dollars currently flowing into AI infrastructure and massive data centers, the wild swings of clean energy ETFs reacting to a single regulatory rumor, or the chaotic, 24/7 global casino of cryptocurrency.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In this hyper-connected environment, the illusion of safety is a dangerous and intoxicating trap. People build complex, highly optimized portfolios that work flawlessly—right up until a sudden geopolitical trade war severs a critical semiconductor supply chain, or an unforeseen algorithmic shift triggers a cascading flash crash across the network.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, having room for error isn't just about keeping a percentage of your portfolio in cash. It is about structural, psychological defense. It means asking yourself what happens if the remote work trend completely reverses, commercial real estate collapses, and the specific tech sector you are heavily exposed to experiences a decade-long winter. A modern margin of safety means recognizing that the unprecedented will happen. If you have leveraged yourself to the absolute maximum to capture the upside of the current AI super-cycle, you have left yourself zero room for error when the inevitable, unpredictable macro-shock arrives, that lack of a safety net will force you to liquidate your positions at the absolute worst time just to survive. You must be skeptical enough to build a massive financial moat today, precisely so you can afford to be optimistic about the decades to come.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "Stress-Test Your Moat",
                                description: "Open the Tracking tab and isolate your highest-conviction assets. Run a mental stress test: If a black swan event cuts the valuation of these assets by 50% for the next three years, does it force you to liquidate your positions to survive? If your lifestyle or liquidity depends on those assets remaining perfectly stable, aggressively reconfigure your exposure to increase your cash buffer.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "Hunt for the Breaking Point",
                                description: "Do not just research why a company will succeed; actively research how it could die. Navigate to your Research tab. Use your Deep Research agent to to identify hidden liabilities, heavy debt burdens, or the bear case. Let the agent uncover the exact scenarios where the company's margin of safety collapses.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 367,
                currentProgress: 0.0
            ),
            6: CoreChapterContent(
                chapterNumber: 6,
                chapterTitle: "The Invisible Chain",
                bookTitle: "The Psychology of Money",
                bookAuthor: "Morgan Housel",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is a reality most people completely misunderstand about capital: we are conditioned to view money strictly as a medium of exchange for physical goods. You earn money to exchange it for a car, a house, a vacation, or a watch. The friction that traps almost every investor is the belief that if you are not actively spending your money on something tangible, that money is sitting idle and useless.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Because of this, people only save with a specific purchase in mind. They save for a down payment. They save for a wedding. But when you only view money as a ticket to acquire things, you inevitably fall victim to lifestyle creep. The moment your income increases, your desires stretch to match it. You upgrade your apartment, you upgrade your car, and suddenly your new, massive income is entirely spoken for. You feel wealthy because you are surrounded by expensive items, but you are actually chained to a higher burn rate. You are forced to work harder, take on more stress, and answer to more people just to maintain the baseline. You have succeeded in acquiring money, but you have failed to acquire the one thing money is actually designed to buy.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Highest Dividend")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author shatters this consumption mindset by revealing what he considers the single greatest intrinsic value of a dollar. He argues that the absolute highest dividend money can possibly pay is the ability to control your own time.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("To illustrate this, the author points to a massive, multi-decade study of elderly populations. When researchers asked thousands of elderly people what the secret to a happy life was, almost none of them said \"working hard to make enough money to buy things.\" None of them cared about the size of their former houses or the prestige of their titles. The overwhelmingly common denominator for happiness was having quality time, strong relationships, and the autonomy to dictate their own schedules.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author explains that the most powerful form of saving is saving for absolutely nothing at all. You do not need a specific reason to save. When you save without a goal, you are no longer saving to buy a product; you are saving to buy options. You are purchasing a hedge against life's inevitable unpredictability.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Every unspent dollar in your bank account represents a tiny piece of your future that you own outright. It represents the ability to take a lower-paying job that you actually love, the ability to walk away from a toxic boss, or the ability to take a six-month sabbatical without your life falling apart. True wealth is waking up in the morning and saying, \"I can do whatever I want today.\"")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Digital Sweatshop")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Let’s translate this into the hyper-accelerated modern era. Look around at the current boom in tech, remote work, and the frantic race to capitalize on artificial intelligence.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("We now have a generation of highly paid \"digital nomads\" and tech executives making staggering amounts of money. But look closely at their lives. A remote worker might be pulling in $300,000 a year, but if they are terrified of being replaced by the latest generative AI model, and are chained to their Slack notifications 24/7 to prove their productivity, they are not free. They have a massive income, but they are living in a digital sweatshop. They have zero autonomy over their Tuesday afternoon.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Conversely, consider someone who built a modest, unglamorous portfolio—perhaps a steady accumulation of basic index funds or stable tech mainstays—but purposely kept their living expenses incredibly low. They don't have the income to charter a private jet, but they have accumulated enough unspent capital to insulate themselves from the AI panic. If their industry shifts or a global pandemic reshapes the economy, they don't have to scramble. They have the flexibility to pause, pivot, and retrain at their own pace.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In today's world, where algorithms can execute trades in milliseconds and remote work blurs the line between the office and the bedroom, your time is under constant siege. If you are using your portfolio just to fund a more expensive lifestyle, you are missing his point. You should be using your portfolio to buy yourself out of the modern grind. Your wealth is measured not by what you can purchase, but by how many months you can truly \"live\" without asking anyone for permission!")
                    ),
                ],
                audioDurationSeconds: 293,
                currentProgress: 0.0
            ),
            7: CoreChapterContent(
                chapterNumber: 7,
                chapterTitle: "The Illusion of the Visible",
                bookTitle: "The Psychology of Money",
                bookAuthor: "Morgan Housel",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is a psychological trap that almost everyone falls into: we rely on visual cues to measure success. If you want to know if someone is physically fit, you look at them. If you want to know if someone is a good public speaker, you listen to them. So, logically, if you want to know if someone is doing well financially, you look at their lifestyle. You look at the car they drive, the zip code they live in, and the photos they post.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The friction arises because this logic is entirely backwards when it comes to finance. The only definitive thing you know about someone driving a $100,000 car is that they have $100,000 less in the bank than they did yesterday—or worse, they are drowning in $100,000 of debt. People completely fail at building long-term security because they are trying to emulate the spending patterns of the rich, completely misunderstanding that true wealth is exactly what you cannot see. When you use your money to signal to the world how much money you have, you are actively destroying the very thing you are trying to display.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Price of the Unbought")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author delivers a masterclass on this distinction by brutally separating two words society uses interchangeably: \"Rich\" and \"Wealthy.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Rich is a current income. It is highly visible. Even someone drowning in debt can be \"rich\" if their monthly cash flow is high enough to cover the interest payments on their massive mansion and sports car.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Wealth, however, is hidden. Wealth is the income not spent. The author tells the story of the singer Rihanna, who nearly went bankrupt and sued her financial advisor. The advisor’s defense was a profound, almost sarcastic truth: \"Was it really necessary to tell her that if you spend money on things, you will end up with the things and not the money?\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author confesses that his own personal financial strategy isn't about maximizing every single yield or driving a spectacular car. He and his family purposefully live a quiet, unglamorous lifestyle with a massive savings rate. Why? Because wealth is the luxury car not purchased. It is the big diamond ring not bought. It is the first-class upgrade declined. Wealth is financial assets that have not yet been converted into the stuff you see. It is a quiet, hidden ledger of options, flexibility, and unspent potential. If you cannot embrace the agonizingly boring act of leaving money alone, you will never build wealth; you will only ever build a facade.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Digital Flex Economy")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Let’s pull this into the hyper-visible reality of the present moment. The pressure to look rich has exponentially mutated. It is no longer just the guy in your neighborhood showing off a new lawnmower; it is a global, 24/7 digital colosseum.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Look at the modern crypto ecosystem, or the explosive wealth narratives surrounding early AI infrastructure investments. Every single day, you are bombarded with screenshots of massive decentralized wallet balances, tech founders flashing their lavish remote-work setups in Bali, or traders showcasing massive overnight gains from an obscure Data Center REIT. This is the modern flex.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But remember the rule of the unseen: you are only seeing the highlight reel. What that viral screenshot does not show you is the crippling margin debt holding that portfolio together. It does not show you the catastrophic tax liability waiting at the end of the year. It does not show you the insomnia.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In an era where everyone can instantly broadcast their perceived success, the desire to convert your unspent capital into visible status is overwhelming. If you acquire a solid, life-changing return from a clean energy ETF, your ego will scream at you to immediately upgrade your lifestyle so the world knows you succeeded. You have to fight this modern instinct with everything you have. The ultimate flex in today's digital economy is having an unassailable, deeply secure financial foundation that absolutely no one on the internet knows about. True wealth is silent.")
                    ),
                ],
                audioDurationSeconds: 269,
                currentProgress: 0.0
            ),
            8: CoreChapterContent(
                chapterNumber: 8,
                chapterTitle: "The Map is Not the Territory",
                bookTitle: "The Psychology of Money",
                bookAuthor: "Morgan Housel",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is the most dangerous assumption embedded in modern finance: the belief that the past is a perfect blueprint for the future. We are conditioned to treat financial history like a hard science. We look at past recessions, past technological revolutions, and past market crashes, and we build elaborate, hyper-detailed models to predict exactly what will happen next. The friction that destroys so many portfolios is the misplaced confidence that if you just study enough historical data, you can eliminate uncertainty.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But people fail because they are studying a world that no longer exists. They build highly rigid, decades-long financial plans assuming two things will remain absolutely static: the macroeconomic environment, and their own personal desires. Both of these assumptions are fatal. When you lock yourself into a rigid, mathematically optimized master plan, you are completely defenseless when the world inevitably does something it has never done before. Even worse, you are trapped when you wake up a decade later and realize the person you are today no longer wants the things the person you were ten years ago was saving for.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Prophet’s Fallacy and the Stranger in the Mirror")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author dismantles this rigid thinking by exposing two profound psychological blind spots. First, he addresses the illusion of history. Economic history is not a map of the future; it is a study of unprecedented, unpredictable surprises. Think about the most consequential economic events of the last century—the Great Depression, World War II, the dot-com bubble, or the 2008 financial crisis. None of these events were historically precedented when they occurred.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("If you only use history as a guide to the future, you act like a prophet who is only looking in the rearview mirror. You will successfully predict the previous crisis, but you will be completely blindsided by the next one, because the next crisis will look entirely different.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author then pairs this with a brilliant concept from psychology: the \"End of History Illusion.\" If you look back at who you were ten years ago, you will easily recognize how drastically your goals, personality, and priorities have changed. However, when we look forward, we suffer from a massive failure of imagination. We almost always assume that who we are today is the finished product. We assume our current career ambitions, risk tolerance, and lifestyle desires will remain permanently fixed until we retire.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("This illusion leads to catastrophic financial rigidity. A young professional might commit to an extreme, punishing frugality strategy to retire at thirty-five, only to realize at thirty-five that they actually love their work and want the capital to start a massive business instead. The author warns that because you cannot predict the global economy, and you cannot even predict your own future personality, avoiding extreme financial commitments is the only way to survive.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The AI Event Horizon and the Shifting Self")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Let’s translate this into the hyper-volatile reality you are navigating right now. Look at the explosion of generative AI, the staggering energy demands of modern Data Centers, and the sudden, decentralized architecture of Crypto.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("If you try to map the current AI infrastructure super-cycle perfectly onto the 1990s internet boom, you will get crushed. The 1990s did not feature the same complex, interwoven geopolitical supply chains, immediate remote-work workforce distributions, or the instantaneous, algorithmic capital flight we see today. We are living in an era where an unexpected trade war over a tiny semiconductor component can freeze an entire global sector overnight. The world is writing new rules in real-time. History cannot save you here.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("More importantly, your relationship to this new economy will evolve. Right now, you might have a high-risk tolerance. You might be perfectly content concentrating your capital into volatile tech ETFs, fully willing to endure brutal 30% swings. But what happens in seven years if you have a family, or decide to move across the country, or suddenly discover a passion for a totally different, lower-paying industry?")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("If your financial architecture is so rigid that it only works if you remain an aggressive, risk-hungry tech investor forever, you are building a prison. True financial mastery in the modern era requires extreme adaptability. You have to build a system that leaves room for the global economy to shock you, and leaves room for you to shock yourself. You must refuse to become a prisoner to your own past predictions.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "Analyze Adaptability, Not Just History",
                                description: "When exploring a new asset, do not just look at its historical chart. Open your Research tab and deploy your Deep Research agent to analyze the company’s structural flexibility. Look for their ability to grow, their moat, and an overview of the big picture from the macroeconomic and geopolitical perspectives. You want to align with companies that are built to adapt to surprises, not just companies that performed well in the past.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 326,
                currentProgress: 0.0
            ),
            9: CoreChapterContent(
                chapterNumber: 9,
                chapterTitle: "The Mirror Trap",
                bookTitle: "The Psychology of Money",
                bookAuthor: "Morgan Housel",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is the invisible trapdoor in the financial world: assuming everyone on the field is playing the exact same sport. Most people fail at wealth creation because they suffer from financial identity theft. They watch a stranger double their net worth in three weeks and immediately feel a sickening wave of FOMO. They assume, subconsciously, that the stranger’s victory is their own missed opportunity. But this is an illusion. The friction arises when you take a financial cue from someone who has radically different risk tolerances, goals, and time horizons than you do. You wouldn't watch a marathon runner sprinting the final 100 yards and decide to adopt that pace for the first mile of your own race. Yet, in the markets, everyday people constantly mimic the moves of institutional sprinters, completely forgetting they are supposed to be running a marathon.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Tale of Two Bubbles")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author tells the story of the late 1990s Dot-Com bubble to illustrate this exact psychological blind spot. Why did otherwise rational, educated people pay utterly unjustifiable prices for internet companies with zero revenue? The common narrative is that people just lost their minds to greed. But the author argues something much more nuanced: the prices actually made perfect sense for the people setting them—the day traders. If a momentum trader acquires a tech stock at $60 with the sole intention of flipping it by lunchtime for $61, they don't care about cash flow, fundamentals, or a ten-year valuation. For their specific, short-term game, the price is entirely rational.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The disaster strikes when a long-term investor, looking to fund a retirement two decades away, sees the day trader's momentum and copies the move. The long-term investor is operating under the delusion that the short-term trader knows something they don't. They take their cues from someone playing a fundamentally different game, and when the bubble bursts, the long-term player is left clutching the ashes while the day trader has long since moved on.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The AI Illusion")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, this trap is infinitely more dangerous because the noise is deafening. We no longer just have day traders; we have high-frequency trading networks, deep learning models, and massive institutional capital dictating market momentum. Consider the current explosion in artificial intelligence and the massive capital flowing into data centers and semiconductor infrastructure. When a quantitative fund deploys a complex deep learning LSTM network or uses cluster analysis to detect micro-anomalies in tech valuations, that machine is optimizing for a game measured in days, hours, or even milliseconds.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("If an everyday investor opens an interface, sees a massive price spike driven by those algorithmic maneuvers, and decides to jump in for their 30-year portfolio, they are committing the ultimate unforced error. They are copying the homework of an entity that is solving an entirely different equation.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The same applies to the rapid rotations into clean energy ETFs or reacting to geopolitical trade-war rumors. The headlines will scream about massive daily gains or steep drops, but those headlines never disclose the time horizon of the people taking the profits or cutting their losses. If you are building wealth for a decade from now, the daily noise of someone optimizing for tomorrow's closing bell is worse than useless to you—it is actively destructive. True financial clarity comes from writing down exactly what game you are playing, and then putting blinders on. You have to clearly define your own financial identity and ruthlessly ignore the noise from everyone else.")
                    ),
                ],
                audioDurationSeconds: 232,
                currentProgress: 0.0
            ),
            10: CoreChapterContent(
                chapterNumber: 10,
                chapterTitle: "The Intellectual Allure of Doom",
                bookTitle: "The Psychology of Money",
                bookAuthor: "Morgan Housel",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Sit in any busy coffee shop, open up your laptop, and scroll through the day's financial headlines. Notice how the articles predicting a catastrophic market collapse are always written by people who sound like absolute geniuses. They use complex macroeconomic jargon, cite geopolitical trade wars, and weave intricate data models to prove that the sky is falling. In contrast, the person quietly saying, \"I think the market will probably be higher in ten years,\" sounds like a naive amateur.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("This is the friction that destroys most portfolios. We are biologically wired to treat pessimism as an intellectual flex. Evolution taught our ancestors that treating a rustle in the bushes as a deadly predator kept them alive, while assuming it was just the wind got them killed. In the financial world, pessimism sounds like someone desperately trying to save your life. Optimism sounds like a cheap, oblivious sales pitch. The reason most people fail at long-term wealth creation is that they let the intellectual seduction of pessimism talk them out of the mathematical reality of human progress.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Adaptation Blindspot")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author highlights a fatal flaw in pessimistic forecasting: it assumes human beings just stand still and blindly take the punishment. When experts predict a crisis, they almost always draw a straight, downward line from today's problem directly into the future.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("What they consistently fail to model is human adaptation. The author points out the profound pessimism that follows major economic crashes or resource shortages. Consider the early 2000s, when the smartest people in the room mathematically \"proved\" we had reached peak oil production and global economies would soon grind to a halt. What the pessimists didn't calculate was that the looming threat of running out of oil created a massive, urgent financial incentive to invent horizontal drilling and fracking. The problem itself funded the solution.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Optimism isn't the delusional belief that everything will be perfect, that crashes won't happen, or that you won't lose money in the short term. True financial optimism is the baseline belief that the odds of a good outcome are in your favor over time, because human beings are incredibly efficient at solving problems when their backs are against the wall. Pessimists extrapolate the disaster; optimists bank on the response.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Cortisol Economy")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, this biological glitch is being ruthlessly exploited. You are not just fighting your own evolutionary wiring; you are fighting algorithms explicitly designed to monetize your anxiety.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Consider the current narrative around the technology sector. On any given day, you can find brilliantly articulated, data-backed reports explaining why the artificial intelligence infrastructure build-out is a massive, unsustainable bubble. You will read deeply researched white papers on how data center energy consumption will collapse the power grid, or how international trade wars will permanently sever the semiconductor supply chain.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("These arguments sound incredibly urgent, smart, and terrifying. But they entirely discount the invisible army of engineers and entrepreneurs currently working 80-hour weeks to optimize GPU efficiency, pioneer next-generation cooling systems, and build resilient, localized supply chains. The media will amplify the looming disaster of an AI bottleneck because panic drives clicks and engagement. But betting on that doom means betting against thousands of incredibly smart people who are incentivized by billions of dollars to solve that exact bottleneck. History shows that betting against human ingenuity is a terrible long-term strategy. The smart money knows that pessimism is for the headlines, but optimism is for the portfolio.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "Demand the boring version",
                                description: "Financial news is mathematically engineered to terrify you into clicking. Open your Updates tab and rely strictly on the AI summaries. Let the system strip out the apocalyptic adjectives and give you the cold, boring facts in three simple bullet points.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 250,
                currentProgress: 0.0
            ),
            11: CoreChapterContent(
                chapterNumber: 11,
                chapterTitle: "The Mirage of Certainty",
                bookTitle: "The Psychology of Money",
                bookAuthor: "Morgan Housel",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Look at the current state of the global market. We are navigating an era of unprecedented chaos—shifting interest rates, volatile geopolitical trade wars, and entire industries being upended overnight by new technologies. In the middle of this storm, human beings are desperate for a map. We crave a predictable world, one where a simple, guaranteed formula leads directly to financial freedom.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("This craving is the invisible friction that destroys so many portfolios. When you desperately need an outcome to be true—because you feel behind on retirement, because inflation is eating your savings, or because you just want to escape the 9-to-5 grind—your brain stops processing cold, hard data. Instead, it starts hunting for a comforting narrative. Most people fail at investing not because they lack intelligence, but because they are easily seduced by storytellers who promise them exactly what they want to hear. They want a predictable universe so badly that they willingly accept financial fictions.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Hallucination of the Oasis")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author highlights a profound psychological vulnerability: the more you want something to be true, the more likely you are to overestimate the odds of it actually being true.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Think of a wanderer lost in the desert. When a person is dying of thirst, they don't just hope there is a lake over the next sand dune; their brain actually fabricates a mirage. They hallucinate an oasis because their body desperately needs water to survive.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The exact same psychological mechanism operates in finance. If a person is 55 years old, severely underfunded for retirement, and terrified of running out of money, they are wandering in a financial desert. When a charismatic fund manager or a loud internet guru pitches them a revolutionary new strategy guaranteeing a 20% annual return, the investor doesn't look at the math. They don't audit the historical failure rate of such promises. They look at the pitch and see an oasis. They believe the story because the alternative—that they have to work for ten more years and drastically cut their standard of living—is too painful to accept. The narrative acts as an anesthetic against reality.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Inevitability Trap")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, the financial deserts are crowded, and the mirages are incredibly sophisticated. We see this vividly in the massive narratives surrounding the Clean Energy transition and the Electric Vehicle (EV) revolution.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The story being aggressively pushed is that a carbon-neutral world is an absolute, inevitable certainty. Therefore, the narrative suggests, investing in any solar manufacturer or new EV startup is a guaranteed, morally righteous path to generational wealth. The story feels so good. It feels like buying into the dawn of the automotive age in 1910.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Because everyday investors desperately want to align their money with their morals—and get rich doing it—they willingly suspend their disbelief. They ignore the cold data: that manufacturing hardware is a brutally low-margin, capital-intensive business. They completely dismiss the historical reality that in massive industrial revolutions, the early pioneers usually go completely bankrupt fighting vicious price wars before the ultimate winners emerge.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The fiction is that a necessary global transition automatically equals a profitable, risk-free investment for you. It doesn't. But when you desperately want to secure your financial future while feeling like you are saving the planet, the \"Green Super-Cycle\" becomes your oasis. You believe the hype not because the financial fundamentals support the valuation, but because you deeply need the story to be true. Protecting your portfolio means realizing that just because an industry changes the world, doesn't mean it will make you rich.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "Interrogate the Valuation",
                                description: "Whenever you feel yourself falling in love with a company's narrative, open the Research tab and generate a Deep Research Report. Don't just at the executive summary. Scroll aggressively past the pros and force yourself to read the Valuation Analysis and the cons list first. If the AI flags the stock as severely \"Overvalued\" with a negative margin of safety, and the risk is too high. Think again!",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "Chat with the Cold Truth",
                                description: "When an internet guru promises a revolutionary new paradigm that \"changes the rules of investing,\" open the Wiser tab to chat with Caudex AI, with a simple question: \"Has this kind of market narrative happened before, and how does it usually end?\". Let the vector-stored logic of history throw cold water on the hot mirage.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 289,
                currentProgress: 0.0
            ),
            12: CoreChapterContent(
                chapterNumber: 12,
                chapterTitle: "The Myth of the Master Key",
                bookTitle: "The Psychology of Money",
                bookAuthor: "Morgan Housel",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Walk into any bookstore or log onto any financial forum, and you will immediately notice a common delusion: everyone is desperately searching for the \"master key.\" The current state of the global market is a chaotic swirl of conflicting data, and in response, a multi-billion-dollar industry has emerged to sell you the illusion of a single, mathematically perfect formula. The friction that destroys everyday investors is the quiet, nagging belief that if they are struggling, it is simply because they haven't discovered the right algorithm or the correct spreadsheet yet.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But personal finance is not physics; there are no universal laws that apply equally to every single person. What works brilliantly for a billionaire hedge fund manager with a forty-year time horizon will completely bankrupt a thirty-year-old teacher saving for a down payment. The failure rate in personal wealth creation has very little to do with poor mathematics. It has everything to do with adopting a strategy that fundamentally clashes with your unique psychological makeup. You cannot borrow someone else's financial philosophy any more than you can borrow their personality.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Sleep Test")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author tells a highly revealing story about his own personal finances to illustrate this exact point. If you were to ask a high-powered supercomputer to design an investment portfolio for a young, high-income financial writer, the machine would output a very specific set of instructions. It would tell him to heavily leverage cheap debt, maximize his risk profile in the markets, and optimize every single dollar for the highest possible yield.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Instead, the author does the exact opposite. He owns his house outright, refusing to carry a mortgage, and he keeps an uncomfortably large portion of his net worth sitting quietly in a standard bank account as plain, boring cash. A purely rational economist would look at this and scream. They would point to the spreadsheets proving that the author is losing out on massive compound interest and allowing inflation to slowly eat away at his cash reserves.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But the author argues that he is not trying to win a spreadsheet competition. He is trying to win the \"sleep test.\" His personal philosophy is not optimized for maximum financial returns; it is optimized for independence and total peace of mind. He knows his own psychology. He knows that if a market crash wiped out his portfolio while he was heavily in debt, he would panic. By paying off his house and holding cash, he has built a financial fortress that prevents him from ever being forced to make a desperate decision. He chose a strategy that is technically sub-optimal in a spreadsheet, but wildly successful in real life, because it allows his family to sleep soundly at night.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Robotic Sky")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, the pressure to abandon your peace of mind for the sake of optimization is louder and more aggressive than ever. Look at the bleeding edge of the market right now: the explosive emergence of autonomous robotics and the rapidly developing eVTOL (electric vertical takeoff and landing) sector.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The narratives surrounding these technologies are intoxicating. Social media feeds are flooded with aggressively smart analysts screaming that if you aren’t heavily exposed to the robotic supply chain or the new aerial mobility infrastructure, you are mathematically dooming your future. They present compelling, undeniable data showing how these sectors will reshape global transportation and labor.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But here is where you must apply the author's logic: if putting a massive chunk of your net worth into a pre-revenue flying car startup makes you check your phone fifty times a day in a cold sweat, it is the wrong strategy for you. It does not matter if the math is right. Even if that specific eVTOL company goes on to completely dominate the sky in 2035, the extreme volatility between now and then will shake you out. You cannot reap the rewards of a mathematical home run if your psychological threshold forces you to abandon the game during the third inning.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Your financial philosophy must act as a titanium filter against the noise of the \"next big thing.\" True wealth management in the modern era is recognizing that a boring, conservative portfolio that you can stick with during a terrifying market crash will infinitely outperform a genius-level robotics portfolio that causes you to panic-exit at the exact wrong moment. You must define your own psychological breaking point and architect your entire financial life to ensure you never cross it.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("Final Words for the Book")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The Psychology of Money reveals the ultimate paradox of finance: the most complex systems in the world are governed by the simplest human emotions. Intelligence, pedigree, and access to sophisticated data algorithms cannot save you from your own ego, fear, or greed. True wealth is never measured by the car in your driveway or the notifications on your screen. It is measured by the invisible, quiet reality of waking up every single morning and knowing that you have total control over your own time. Master your psychology, respect the role of luck, build an unbreakable margin of safety, and you will achieve the only financial goal that actually matters: absolute freedom.")
                    ),
                ],
                audioDurationSeconds: 345,
                currentProgress: 0.0
            ),
        ],
        4: [
            1: CoreChapterContent(
                chapterNumber: 1,
                chapterTitle: "Flipping the Script on Wall Street",
                bookTitle: "One Up On Wall Street",
                bookAuthor: "Peter Lynch",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"Smart Money\" Myth")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Come closer. Let’s be honest about why you’re nervous. You think the game is rigged. You look at the guys in the glass towers with their Bloomberg terminals, high-frequency trading algorithms, and billion-dollar research budgets, and you feel like a minnow swimming with sharks. You assume that to win, you need to play their game. But that is exactly why most retail investors get slaughtered—they try to imitate the \"pros\" without realizing that the pros are actually handcuffed.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Institutional Handcuffs")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Peter Lynch, the legend who ran the Magellan Fund, used to laugh about this. He described the \"institutional imperative\" that paralyzes professional fund managers. Imagine a fund manager at a cocktail party. He explains that he can’t just buy a stock because it’s profitable; he has to buy what is \"acceptable.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Lynch famously noted that \"You never get fired for losing money on IBM.\" If a manager buys IBM and it tanks, his boss shrugs—everyone holds IBM. But if that same manager buys a small, obscure company like a donut chain or a motel franchise, and that tanks? He’s fired for gambling. By the time an institution is \"allowed\" to buy a high-growth stock, the company has usually been performing well for years. The \"Smart Money\" is actually the \"Late Money.\" They are forced to wait until the coast is clear, leaving the biggest gains on the table for the flexible amateur who saw the lines out the door three years earlier.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The ETF Blindspot")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("This dynamic is even more aggressive in the modern era. Today, the market is dominated by passive ETFs and Index Funds. These funds operate on rigid rules—they must buy the biggest companies (like Apple or NVIDIA) regardless of valuation, and they cannot touch companies below a certain market cap.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Think about the early days of Zoom or specialized AI tools. You might use a new piece of software at work every day, loving how it solves a problem. Meanwhile, Wall Street analysts can’t cover it yet because it’s too small, and the ETFs can’t buy it because it’s not in the S&P 500. This creates a massive inefficiency. While the algorithms are fighting over fractions of a penny on Tesla, you have free reign in the under-followed small-cap market. Your lack of a \"compliance department\" is your greatest competitive advantage.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "Stop \"Playing Professional\"",
                                description: "If you are reading analyst reports from Goldman Sachs to make decisions, stop. You are reading yesterday’s news.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "Identify the Institutional Blindspot",
                                description: "Look at the products you use that are essential but \"boring\" or too small for the news to cover. If an ETF can't buy it yet, put it on your watchlist.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 183,
                currentProgress: 0.0
            ),
            2: CoreChapterContent(
                chapterNumber: 2,
                chapterTitle: "The Mirror Test & Risk Tolerance",
                bookTitle: "One Up On Wall Street",
                bookAuthor: "Peter Lynch",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Casino Fallacy")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Most people treat the stock market like a casino, hoping for a lucky spin, but panicking the moment the dealer takes a chip. They buy high because their neighbor made a killing, and sell low because the news anchor looked worried. They fail not because they lack intelligence or data, but because they lack stomach. They haven't asked themselves the only question that matters before opening a brokerage account: \"What will I do when—not if—my portfolio drops 50%?\" If you don't have an answer, you are already the market's prey.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Stomach Over the Brain")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Peter Lynch calls this \"The Mirror Test.\" Before buying a single share, you must stand in front of a mirror and ask yourself three questions: \"Do I own a house? Do I need the money before I can get it back? Do I have the personal qualities that will bring me success?\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Lynch tells the story of the frantic calls he’d get during market crashes. Even when his fund, Magellan, was the best performing in the world, the average investor in his fund actually lost money. Why? because they bought in when he was on the cover of a magazine and sold out the moment the market dipped. They treated the stock market like a savings account that pays 15% interest. It doesn't. He explains that the most important organ for investing isn't the brain; it's the stomach. If you are susceptible to selling everything in a panic because \"The Big One\" is coming, you should never buy a stock in the first place. You are better off in a money market fund than being right about a company but wrong about your own psychology.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Gamification Trap")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("This \"stomach\" problem has only intensified in the age of instant information and gamified trading apps. In Lynch's day, you had to call a broker to sell; that friction saved people from themselves. Today, you can liquidate your life savings from a notification on your watch while waiting for a latte.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Consider the \"Crypto crashes\" or the meme-stock frenzies. Investors with high conviction in the technology (like blockchain) often get flushed out by 80% drawdowns because they used leverage or money they needed for next month's rent. The volatility is the price of admission for the returns. If you are constantly checking your portfolio app, getting a dopamine hit when it's green and a cortisol spike when it's red, you are failing the Mirror Test every single day. You are confusing volatility (the price moving around) with risk (permanent loss of capital).")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Sleep Well\" Check",
                                description: "If you wake up worrying about a stock, sell down to the \"sleeping point.\"",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "Segregate Funds",
                                description: "Never invest money you will need in the next 3-5 years. The market is a vehicle for wealth creation, not a substitute for an emergency fund.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 194,
                currentProgress: 0.0
            ),
            3: CoreChapterContent(
                chapterNumber: 3,
                chapterTitle: "Ignoring the Macro Noise",
                bookTitle: "One Up On Wall Street",
                bookAuthor: "Peter Lynch",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Prediction Addiction")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The single biggest waste of time for any investor is trying to predict the economy. You sit there, paralyzed, watching CNBC, terrified because the Fed might raise rates by a quarter point or because some pundit says a recession is \"imminent\" (they’ve been saying that for six years). You think you’re being responsible by waiting for the \"right time\" when the skies clear. But here is the brutal truth: the skies never clear. There is always something to worry about. If you spend 13 minutes a year thinking about economics, you've wasted 10 minutes.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Cocktail Party Indicator")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Peter Lynch loved to poke fun at the \"The Great Worry.\" He tells the story of how, in the 1950s, people were terrified of a nuclear war and a depression; in the 1970s, it was oil prices; in the 1980s, it was Japan taking over the world. There was never a year where a sensible person couldn’t find a dozen logical reasons to sell everything and hide under the bed.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Lynch illustrated this with his \"Cocktail Party Theory.\" When he’d go to a party during a market bottom, no one wanted to talk about stocks; they talked about dentistry or the weather. When the market was up 15%, they’d ask him for tips. When the market was at a dangerous peak, they were giving him tips. His point was simple: the crowd is always reacting to what has happened, not what will happen. He famously said, \"If you could predict the economy, you'd be a billionaire.\" Since you can't, you must ignore the macro and focus entirely on the micro: the specific earnings of the company in front of you.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Doom-Scrolling Tax")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("This is even harder today because the \"noise\" has become a deafening roar. In Lynch's time, you had the evening news. Today, you have a 24/7 feed of \"doom-scrolling\" on X (Twitter) and YouTube thumbnails screaming \"MARKET COLLAPSE IMMINENT!\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Think about the COVID-19 crash. Macro-economists predicted a decade-long depression. If you listened to the macro noise, you sold everything. But if you ignored the economy and looked at the micro, you saw that companies like Zoom, Peloton, and Amazon were seeing explosive, unprecedented demand. The economy was closed, but specific businesses were booming. The same applies to AI today. While pundits argue about a \"soft landing\" or \"hard landing\" for the GDP, specific companies are deploying LLMs and revolutionizing productivity regardless of what the Fed does next week.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "Focus on the Business",
                                description: "When you feel panic about the \"market,\" force yourself to look at the earnings report of your favorite holding. Is the company still making money? If yes, close the laptop.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 182,
                currentProgress: 0.0
            ),
            4: CoreChapterContent(
                chapterNumber: 4,
                chapterTitle: "Leveraging Your Daily Routine",
                bookTitle: "One Up On Wall Street",
                bookAuthor: "Peter Lynch",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Wall Street Gatekeeper")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("You probably think the best stock tips are hidden in a locked room on Wall Street, guarded by men in $5,000 suits whispering about \"proprietary data.\" So you ignore the most powerful investment tool you own: your eyes. You walk right past the next Apple or Starbucks every single day, use their products, complain about their competitors, and then go home to buy a stock you’ve never heard of because a stranger on the internet said it was \"ready to pop.\" This is madness. The best research isn't on a spreadsheet; it's in your shopping cart.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Pantyhose Advantage")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Peter Lynch loved to humiliate the professional class with this concept. He tells the story of Hanes, the company that made L’eggs pantyhose. In the 1970s, Wall Street analysts were busy crunching numbers on steel mills and solar energy. Meanwhile, Lynch’s wife, Carolyn, came home raving about these new pantyhose sold in plastic eggs at the grocery store. They fit better, they were convenient, and she bought them every week.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Lynch didn't run a complex discounted cash flow model initially. He just looked at the checkout line. He saw women buying them in droves. He realized that Hanes had a monopoly on the \"supermarket pantyhose\" niche. While the \"smart money\" was ignoring a boring clothing company, the stock went up six-fold. He applied the same logic to Dunkin’ Donuts. He didn't need an annual report to tell him the coffee was good; he just saw that the parking lot was full at 6:00 AM every single morning. If you like the product, chances are millions of others do too.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"User Experience\" Alpha")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the digital age, this \"local knowledge\" advantage has exploded. You don't just shop at the mall; you work in specialized software environments and live in digital ecosystems. Think about the rise of Slack or CrowdStrike. Who knew about these first? Not the bankers. It was the IT professionals and office workers who started using them and realized, \"Wow, this is actually essential.\" Or consider the Ozempic/Wegovy craze. Long before the stock chart went parabolic, ordinary people in gyms and doctor’s offices were whispering about a \"miracle shot\" for weight loss. If you were a receptionist at a clinic or just a person struggling with weight, you had the \"insider info\" months before the hedge funds caught on. Your job, your hobbies, and your subscriptions are a goldmine of data that Wall Street can't access until the quarterly report comes out.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "Audit Your Credit Card",
                                description: "Look at your last three months of spending. Which recurring charge (subscription, product, brand) would you be least likely to cancel? That is a stock lead.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The \"Workplace Edge\"",
                                description: "What software or hardware does your company buy that actually works? What is the industry standard that everyone hates? (Short candidate).",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The \"Kid Test\"",
                                description: "If you have children, watch what they are addicted to (Roblox, TikTok, specific snacks). They are the ultimate trend forecasters.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 201,
                currentProgress: 0.0
            ),
            5: CoreChapterContent(
                chapterNumber: 5,
                chapterTitle: "The \"Perfect Stock\" Profile",
                bookTitle: "One Up On Wall Street",
                bookAuthor: "Peter Lynch",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Seduction of \"Sexy\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("You are addicted to \"sexy.\" You want to own the company that’s curing cancer, mining asteroids, or inventing the next iPhone. You want a stock ticker you can drop into a conversation at a dinner party that makes you look sophisticated and plugged-in. But here is the secret that will make you rich while your sophisticated friends go broke: excitement is expensive. If a company is \"hot,\" it’s already overpriced. The path to massive wealth is paved with boredom, disgust, and toxic waste.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Funeral Home Moat")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Peter Lynch didn’t just tolerate boring companies; he fetishized them. He tells the story of his absolute dream stock: Service Corporation International (SCI). What did they do? They buried people. They were a funeral home chain.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Lynch loved SCI because it hit every single one of his \"Perfect Stock\" criteria: it had a boring name, it did something disagreeable (nobody likes thinking about death), and rumors about the industry were depressing. Wall Street analysts refused to cover it because they didn't want to call their clients and say, \"I've got a great deal on coffins.\" Because the pros ignored it, the stock price stayed low relative to its earnings for years, allowing Lynch to buy in cheap while the company quietly bought up mom-and-pop funeral homes across the country, compounding its value in the shadows. He jokingly said if he could find a company with a name like \"Bob’s Toxic Waste & Septic Tank Cleaning,\" he’d buy it immediately.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Boredom Arbitrage")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("This \"Boredom Arbitrage\" is even more potent today because the \"hype cycle\" moves faster than ever. When \"AI\" became the buzzword, stocks like NVIDIA and Microsoft skyrocketed instantly. The premiums were priced in within seconds.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But look at the \"plumbing\" of the modern world. While everyone was betting on volatile crypto exchanges, companies like Waste Management (WM) or Cintas (CTAS) (uniform rentals) were printing money. Think about Costco. It’s a warehouse. It sells bulk toilet paper. It is the definition of unsexy. Yet, it has crushed the performance of most \"disruptive\" tech stocks over the long run because it dominates a niche, has a recurring revenue model (memberships), and is too boring for the \"get rich quick\" crowd to pump and dump. In the software world, look for the unsexy B2B SaaS companies that handle payroll or compliance—the stuff that has to happen for the economy to function, regardless of whether ChatGPT takes over the world.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Gross\" Factor",
                                description: "actively search for industries people hate: garbage, sewage, funerals, grease collection.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 173,
                currentProgress: 0.0
            ),
            6: CoreChapterContent(
                chapterNumber: 6,
                chapterTitle: "The Six Categories of Opportunity",
                bookTitle: "One Up On Wall Street",
                bookAuthor: "Peter Lynch",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The One-Size-Fits-All Error")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("You are judging a fish by its ability to climb a tree. You buy a utility company and get angry when it doesn't double in six months. You buy a high-growth tech stock and panic when it drops 20% in a week. You are losing money because you are applying the same rules to completely different games. You treat every ticker symbol as a \"stock,\" but that is like treating a racehorse, a dairy cow, and a house cat as just \"animals.\" If you try to milk the racehorse, you’re going to get kicked.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Taxonomy of Profit")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Peter Lynch stopped this madness by creating a strict taxonomy. Before he bought a single share, he forced the company into one of six boxes: Slow Growers, Stalwarts, Fast Growers, Cyclicals, Asset Plays, or Turnarounds.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He tells the story of how this saved him with the auto industry. Most people treated car companies like \"Stalwarts\" (steady reliable growers like Coca-Cola). Lynch recognized them as \"Cyclicals\"—companies whose fortunes rise and fall with the economy. When Ford was booming, amateurs bought more, thinking the growth was permanent. Lynch sold, knowing the cycle would inevitably turn. He viewed \"Fast Growers\" (like Taco Bell in its early days) as the only ones worth holding through volatility, whereas \"Turnarounds\" (like Chrysler) were pure plays on survival, not growth. If you don't know which category your stock is in, you don't know when to sell.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Identity Crisis")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the modern market, misclassification is the primary cause of portfolio death. Look at Zoom or Peloton during the pandemic. Millions of investors mistook them for \"Fast Growers\" (permanent secular trends). In reality, they were \"Cyclicals\" tied to the \"stay-at-home\" cycle. When the world reopened, the cycle turned, and the stocks collapsed. The same applies to Crypto Miners. They are not \"tech growth stocks\"; they are commodity cyclicals tied to the price of Bitcoin. If Bitcoin drops, their earnings vanish. Conversely, Microsoft and Apple have transitioned from Fast Growers to \"Stalwarts.\" They won't make you rich overnight anymore, but they offer recession protection that a small-cap AI startup simply cannot.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("Match the Strategy")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Cyclicals (Chips, Energy, Crypto): Sell when P/E is low (earnings are at peak).")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Fast Growers (AI, SaaS): Hold as long as the earnings growth story is intact.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Stalwarts (Big Tech): Sell if the valuation gets absurd (e.g., 80x earnings).")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "Audit Your Portfolio",
                                description: "Go through every stock you own and write one of the six categories next to it. If you can't categorize it, sell it.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 172,
                currentProgress: 0.0
            ),
            7: CoreChapterContent(
                chapterNumber: 7,
                chapterTitle: "The Earnings Engine",
                bookTitle: "One Up On Wall Street",
                bookAuthor: "Peter Lynch",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Price Tag Distraction")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("You are hypnotized by the wrong number. You wake up, check your phone, and see a stock price. It’s up $5. You feel brilliant. It’s down $10. You feel like a failure. You are letting the market’s mood swings dictate your emotional state. This is because you view a stock as a piece of paper that magically changes value, rather than what it actually is: a claim on a stream of cash. If you don't know exactly how the company makes a dollar of profit, you are walking blindfolded into traffic.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Two-Minute Drill")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Peter Lynch had a simple cure for price-fixation: the \"Two-Minute Drill.\" Before buying, he forced himself to deliver a monologue. If he couldn't explain to a child in two minutes why the company's earnings were going to go up, he wouldn't buy.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He loved to show charts where the stock price line wobbled all over the place like a drunk, while the earnings line climbed steadily upward like a staircase. His point? The drunk (price) eventually has to come home to the staircase (earnings). If a company’s earnings grow by 20% a year for a decade, the stock price will eventually follow, regardless of wars, recessions, or bad moods on Wall Street. He famously said, \"If you can’t predict the earnings, you can’t predict the stock.\" He didn't care about the \"story\" unless the story ended in a check being cashed.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"Profitless Prosperity\" Myth")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the modern era, Silicon Valley has tried to convince you that \"Earnings don't matter\"—that \"Growth\" and \"Users\" and \"Total Addressable Market\" are the new metrics. This is a trap. Look at the SaaS (Software as a Service) boom of 2020-2021. Companies like Snowflake or Asana were trading at 50x or 100x revenue while losing millions of dollars. Investors said, \"It's a new paradigm!\" Then interest rates rose, and the floor fell out. The stocks that survived and thrived (like Microsoft or Alphabet) were the ones with massive, undeniable profits. Even Uber, the poster child for \"growth at all costs,\" only became a stable investment once it proved it could actually generate positive cash flow. The \"earnings engine\" is the only thing that keeps a stock afloat when the hype cycle ends.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The Monologue Test",
                                description: "Stand in front of a mirror. Say: \"I am buying [Stock] because earnings will grow due to [Reason X], and the P/E ratio is [Y].\" If you stutter, don't buy.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "Check the \"E\"",
                                description: "If a company has no earnings (P/E is \"N/A\"), you are a venture capitalist, not an investor. Size your position accordingly (small).",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 176,
                currentProgress: 0.0
            ),
            8: CoreChapterContent(
                chapterNumber: 8,
                chapterTitle: "Financial Forensics",
                bookTitle: "One Up On Wall Street",
                bookAuthor: "Peter Lynch",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Balance Sheet Blindness")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("You are driving a sports car at 150 miles per hour, but you’ve taped over the fuel gauge because \"math is boring.\" You love the speed—the rising stock price, the hype, the revolutionary technology. But you are ignoring the only thing that can kill you instantly: running out of gas.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Most investors treat the Balance Sheet like a terms-and-conditions document—they scroll right past it. They assume that if a company is \"changing the world,\" the money will sort itself out. This is a fatal error. Companies don't go bankrupt because they lack vision; they go bankrupt because they run out of cash. If you cannot spot the difference between a company that is \"investing in growth\" and one that is bleeding to death, you are not an investor; you are a philanthropist for bad management.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Debt Detective")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Peter Lynch approached the balance sheet not like an accountant, but like a detective at a crime scene. He wasn't looking for \"perfect\" numbers; he was looking for the \"murder weapon\"—usually Debt.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He tells the story of Chrysler in the early 1980s. Everyone thought the company was dead. The stock was trading at miserable lows. But Lynch dug into the debt structure and found a crucial detail that the market missed: Chrysler’s debt was mostly \"funded debt\" (long-term bonds), not \"bank debt\" (loans the bank can call in immediately). This meant Chrysler had time to turn the ship around. The banks couldn't force them into bankruptcy overnight.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He also obsessed over Inventory. Lynch would look at a company’s sales growth versus its inventory growth. If sales were up 10% but inventory was up 30%, he ran for the exit. Why? Because it meant the company was stuffing warehouses with products nobody wanted. In the retail world, unsold inventory is like rotting fish—the longer it sits, the more it stinks, and eventually, you have to pay someone to take it away (mark it down to zero). He viewed a buildup of inventory as the single most reliable \"sell signal\" in the market, often appearing months before the stock price collapsed.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The ZIRP Zombie Apocalypse")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the last decade, we lived through a \"Zero Interest Rate Policy\" (ZIRP) fantasy world where debt was free. This created an entire generation of \"Zombie Companies\"—tech startups and growth stocks that survived only by constantly borrowing cheap money or issuing new shares. They had great revenue growth but terrible balance sheets.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Now that interest rates have normalized, the tide has gone out, and we can see who is swimming naked. Look at the collapse of WeWork or the struggles of Peloton. These companies had incredible \"stories\" and \"revenue growth,\" but their balance sheets were ticking time bombs. They were burning cash faster than they could bring it in.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, \"Financial Forensics\" means looking past the \"Adjusted EBITDA\" (which Charlie Munger famously called \"bullshit earnings\") and looking at Free Cash Flow. In the SaaS (Software as a Service) world, look at the \"Burn Rate.\" If a company has $100 million in the bank but burns $50 million a quarter, they have six months to live. No amount of \"AI integration\" announcements can save a company that cannot pay its server bills.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Net Cash\" Calculation",
                                description: "Before buying, take Cash & Equivalents and subtract Long-Term Debt. If the number is positive, the company has a \"fortress balance sheet\" (like Google or Apple). If it’s deeply negative, check the interest rate on that debt.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The Inventory Red Flag",
                                description: "Check the last quarterly report. Did \"Inventory\" grow faster than \"Revenue\"? If yes, be careful.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "Ignore \"Adjusted\" Metrics",
                                description: "If a company highlights \"Adjusted EBITDA\" but their \"Net Income\" is negative, assume they are losing money. Real money doesn't need to be \"adjusted.\"",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 252,
                currentProgress: 0.0
            ),
            9: CoreChapterContent(
                chapterNumber: 9,
                chapterTitle: "Assessing Management & Dividends",
                bookTitle: "One Up On Wall Street",
                bookAuthor: "Peter Lynch",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Empire Builder Complex")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("You love a charismatic CEO. You watch them on TV, radiating confidence, talking about \"synergies\" and \"ecosystems,\" and you think, \"This guy is a visionary.\" You are falling for the oldest trick in the book. Most managers, when left unsupervised with a pile of cash, do not act like responsible stewards; they act like emperors. They get bored with the core business that made them rich and start looking for excitement. They want to buy a movie studio, a sports team, or a competitor they don't understand, just to make their empire bigger. And they use your money to do it.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Diworsification Disease")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("Peter Lynch had a name for this disease: \"Diworsification.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He tells the story of companies that were printing money in a boring, profitable niche (like making razors or cigarettes) and then decided to \"diversify\" by buying completely unrelated businesses—like a razor company buying a turkey farm. Lynch viewed a high dividend not just as a payout, but as a \"discipline.\" If a company commits to paying a dividend, the CEO can't waste that cash on a foolish acquisition. The money is gone; it’s in your pocket.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He loved the \"dull\" management teams that simply raised the dividend every year. It was a signal that the business was real. He famously said that while companies can fake earnings with accounting tricks, \"dividends don't lie.\" You either have the cash to pay them, or you don't.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Buyback Charade")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the modern tech world, \"Diworsification\" has a new face: \"Moonshots.\" Look at Meta (Facebook) in 2021-2022. They were generating billions in ad revenue, but instead of returning it to shareholders, they burned billions trying to build the \"Metaverse.\" The stock collapsed. It was only when Mark Zuckerberg cut costs and initiated a dividend in 2024 that the market truly forgave him.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, the \"Dividend\" has a quieter, more tax-efficient cousin: the Stock Buyback. Companies like Apple don't just pay dividends; they use their massive cash piles to buy their own shares, reducing the share count and making your remaining shares more valuable. However, you must watch out for the \"fake buyback\"—where a company buys back stock just to offset the millions of shares they gave to employees as \"Stock-Based Compensation.\" That isn't returning capital; it's just treading water.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Insider\" Check",
                                description: "Before you buy, check if the insiders (CEO, CFO) are buying with their own money. Lynch said, \"Insiders might sell for many reasons (divorce, taxes), but they only buy for one: they think the price is going up.\"",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 174,
                currentProgress: 0.0
            ),
            10: CoreChapterContent(
                chapterNumber: 10,
                chapterTitle: "Designing the Allocation",
                bookTitle: "One Up On Wall Street",
                bookAuthor: "Peter Lynch",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("Watering the Weeds")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("You are likely guilty of the most destructive habit in investing: \"Cutting the flowers and watering the weeds.\" You buy a stock, and it goes up 40%. You get nervous. You think, \"I better sell and lock in the profit before it disappears.\" Then you look at your other stock that is down 40%. You think, \"I can’t sell this one at a loss; I’ll wait for it to come back.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("So, you sell the winner and keep the loser. You have just penalized success and rewarded failure. Over time, this leaves you with a portfolio full of stagnant \"weeds\" and zero \"flowers.\" You are managing your emotions, not your money.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Concentration Edge")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Peter Lynch viewed portfolio management as gardening. He argued that there is no magic number of stocks to own, but there is a magic rule: only own what you can follow. He mocked the idea of \"diversification\" for its own sake, calling it \"diworsification.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("If you have a distinct \"edge\" in only one company, own one. If you find five great situations, own five. But don't buy a 10th stock just to \"spread risk\" if you don't know anything about it. Lynch’s strategy was to constantly rotate capital based on the \"story,\" not the price. If a \"Stalwart\" (steady grower) went up 50% and got expensive, he would sell it to buy a \"Fast Grower\" or a \"Turnaround\" that was just starting its journey. He didn't sell because the price was up; he sold because the opportunity had shifted to a better category.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"Fake Diversification\" Trap")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the modern ETF era, investors have fooled themselves into thinking they are diversified when they are actually just \"closet indexers.\" You might own an S&P 500 ETF, a \"Tech Growth\" ETF, and a \"US Momentum\" ETF. You think you have three distinct assets. In reality, all three are heavily weighted in NVIDIA, Microsoft, and Apple. You haven't diversified; you’ve just tripled your exposure to the same five companies.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Similarly, \"Crypto Diversification\" is often a myth. Buying 20 different \"altcoins\" isn't diversification if they all crash 90% the moment Bitcoin sneezes. True allocation today means understanding correlation. If everything in your portfolio turns green on the same day, you aren't diversified; you're just lucky. A properly designed portfolio should have parts that zig when others zag—like holding cash or short-term treasuries to deploy when the \"Fast Growers\" crash.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The 5-Stock Rule",
                                description: "Can you name 5 specific reasons why you own a stock? If you have 20 stocks, you probably can't do this for all of them. Consolidate.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 176,
                currentProgress: 0.0
            ),
            11: CoreChapterContent(
                chapterNumber: 11,
                chapterTitle: "The Re-Evaluation Loop",
                bookTitle: "One Up On Wall Street",
                bookAuthor: "Peter Lynch",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Loyalty Penalty")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("You treat your stocks like a marriage, sticking with them \"for better or worse\" long after the love is gone. You bought a company because it had a great new product three years ago. Today, competitors have flooded the market, the CEO quit, and profit margins are shrinking. Yet you still hold on, paralyzed by nostalgia. You are investing in a memory, not a business. The most dangerous phrase in your vocabulary is \"I’ll just hold it for the long term,\" because \"long term\" is often just a euphemism for \"I’m too lazy to check if the thesis is broken.\"")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Life-Cycle Audit")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Peter Lynch didn't \"buy and hold\"; he \"bought and watched.\" He treated his portfolio like a constantly evolving narrative. He tells the story of Holiday Inn. In the 1950s and 60s, it was a classic \"Fast Grower.\" You could travel across America and see them popping up at every exit. The stock was a rocket ship. But by the mid-70s, the growth phase was mathematically over—they were everywhere. There was no more room to build.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Lynch realized the \"story\" had changed. The company wasn't dying, but it had morphed from a sprinter into a slow jogger. The investors who kept expecting \"Fast Grower\" returns from a saturated \"Stalwart\" got crushed. He instituted a \"Two-Minute Drill\" re-check every few months. If the story had drifted—like a company moving from \"high growth\" to \"cyclical\"—he didn't hesitate to sell, even if he liked the stock. He knew that when the story ends, you have to leave the theater.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Disruption Clock")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the modern world, \"stories\" rot faster than ever. Technology accelerates the lifecycle of companies. Think about BlackBerry. For years, it was the standard for business communication. It was a \"Fast Grower.\" Then the iPhone launched. The story didn't just change; it evaporated overnight. Investors who re-evaluated the \"moat\" (physical keyboards vs. touchscreens) got out. Those who relied on past glory (\"Everyone uses BBM!\") lost everything. The same happened with Peloton. The \"story\" was: \"Everyone is working out at home forever.\" Then gyms reopened. The story broke. If you didn't re-evaluate immediately when vaccines rolled out, you rode the stock all the way down. Today, you must watch for \"AI Disruption.\" Is your \"Stalwart\" software company about to be automated away by a Large Language Model? If the fundamental \"why\" of your investment is threatened by a new technology, you must sell before the earnings report confirms the damage.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The Quarterly Audit",
                                description: "Every 3 months (earnings season), open your portfolio. Has a new competitor appeared?",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The \"Thesis Check\"",
                                description: "Write down your original reason for buying. (e.g., \"Buying because of 20% growth in cloud division\"). Is that specific sentence still true today?",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 185,
                currentProgress: 0.0
            ),
            12: CoreChapterContent(
                chapterNumber: 12,
                chapterTitle: "The Exit Protocols",
                bookTitle: "One Up On Wall Street",
                bookAuthor: "Peter Lynch",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Emotional Inversion")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Buying a stock is like falling in love—it’s exciting, full of promise, and driven by dopamine. Selling a stock is like a divorce—it’s painful, confusing, and driven by fear. Most investors are terrible at \"the breakup.\" You sell your winners too early because you’re terrified the profit will vanish (locking in a small gain), and you hold your losers too long because you can’t admit you were wrong (praying for a rebound). You are emotionally inverted: you are loyal to the things that hurt you and fickle with the things that help you. You need a prenup for your portfolio.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Cyclical Peak")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Peter Lynch didn’t believe in \"target prices.\" He believed in \"Category Signals.\" He realized that the signal to sell a fast-growing restaurant chain is completely different from the signal to sell a boring chemical company.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He tells the story of the Cyclical Trap. Amateur investors love to buy Cyclicals (like auto companies or steel mills) when the P/E ratio is low, thinking they are getting a bargain. Lynch whispered the exact opposite: \"Sell the Cyclical when the P/E is low.\" Why? Because for a Cyclical, a low P/E means earnings are at a record high, the economy is booming, and everything is perfect. This is the top. The only place to go is down. Conversely, he advised holding Fast Growers (like a young Walmart or Taco Bell) even if the P/E looked high, provided the growth story was still intact. As long as they were opening new stores and same-store sales were rising, he held on. He didn't sell because the stock went up; he sold only when the company stopped growing or the valuation became mathematically impossible (like a Stalwart trading at 50x earnings).")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Algorithmic Front-Run")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In today’s hyper-speed market, \"The Exit\" is even more critical because algorithms punish missed expectations instantly. Look at the Semiconductor Industry (NVIDIA, AMD). These are the ultimate modern Cyclicals. When demand for AI chips is insatiable, their P/E ratios compress because earnings explode. That is precisely the danger zone. If you wait for the headlines to say \"Chip Shortage Over,\" the stock will have already dropped 40%. The market \"front-runs\" the cycle. Similarly, look at Crypto. The \"Sell the News\" phenomenon is real. When the Bitcoin ETF was finally approved in early 2024, the price dropped. Why? Because the anticipation was the trade. Once the event happened, the smart money exited into the liquidity provided by the retail crowd buying the headline. And for the \"COVID Winners\" (Zoom, DocuSign): The sell signal wasn't the price; it was the return to the office. The moment the behavioral trend shifted, the \"Fast Grower\" thesis died, and the exit protocol should have triggered immediately, regardless of the loss.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("Final Words")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Wall Street has spent billions constructing an illusion designed to intimidate you, desperate to make you believe that beating the market requires a team of analysts, expensive terminals, or complex deep learning models predicting price movements. In reality, their massive size and rigid institutional compliance rules are their fatal flaws, forcing them to buy late and locking them out of the most lucrative early-stage growth. By stripping away the macroeconomic noise and ignoring the seduction of \"sexy\" trends, you’ve discovered that your greatest competitive moat is your own daily life. The \"Smart Money\" is handcuffed, but you—the observant amateur—have the agility to spot the next massive winner in your shopping cart, your software stack, or your local neighborhood long before the index funds are allowed to buy in.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Tomorrow morning, when the opening bell rings and the financial networks scream about the next imminent crisis, you are no longer the market's prey. You now operate with a ruthless, resilient system: you know how to leverage your local knowledge, run the financial forensics on an earnings engine, and execute strict exit protocols based on a company's true category rather than its stock price. You are no longer playing their gamified, high-frequency casino; you are playing the quiet, patient game of business ownership. So, trust the balance sheet, and just look around you—the edge has been yours the whole time, and now you finally know how to master it.")
                    ),
                ],
                audioDurationSeconds: 281,
                currentProgress: 0.0
            ),
        ],
        5: [
            1: CoreChapterContent(
                chapterNumber: 1,
                chapterTitle: "The 'Scuttlebutt' Investigation",
                bookTitle: "Common Stocks and Uncommon Profits",
                bookAuthor: "Philip Fisher",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"Sanitized\" Trap")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is the uncomfortable truth: if you are making investment decisions based solely on annual reports or CNBC headlines, you are already the \"sucker at the table.\" Why? Because that information has been sanitized by lawyers, polished by PR firms, and digested by millions of algorithms before you ever see it. Most investors fail because they analyze a stock like a consumer reading a menu. They look at the price and the description, assuming what is listed is exactly what will be served. But the real story—the one that drives 10x returns or prevents catastrophic losses—is never on the menu. It’s in the kitchen.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Gossip Network")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Philip Fisher didn’t trust the menu. He coined the term \"Scuttlebutt\"—an old naval term for the gossip around the water cooler. Fisher’s genius wasn't in analyzing balance sheets, but in acting like a detective. He believed that the most honest information about a company (Company A) never came from Company A itself.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Instead, Fisher would go to Company B—their fiercest competitor—and ask, \"What is Company A doing that worries you?\" He would talk to suppliers, customers, and even former employees. He found that while a CEO might lie about their growth, a competitor will never lie about who they are losing customers to. This triangulation of \"gossip\" creates a 3D picture of reality that a spreadsheet never could.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("Digital Forensics")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Fisher had to rely on telephone calls and lunch meetings; you have a supercomputer in your pocket. Today, \"Scuttlebutt\" is digital forensics.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Employee Sentiment: Don’t just read the ESG report. Go to Glassdoor or Blind. Are the engineers at that AI startup complaining about \"spaghetti code\" and high turnover? That’s a sell signal no earnings call will reveal.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Customer Reality: If you’re looking at a consumer brand, ignore the projected sales deck. Go to Reddit or Twitter/X. Are early adopters raving about the product, or are they complaining about shipping delays?")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Supply Chain Checks: In the age of global logistics, tracking import data or supplier contracts can tell you if a company is actually ramping up production before they announce it.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Competitor Check\"",
                                description: "Before buying a stock, find its top two competitors. Search for interviews with their executives. If they don't mention your target company as a threat, do not buy it.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The \"Ex-Employee\" Filter",
                                description: "Search LinkedIn for people who left the company in the last 6 months. High churn in the sales or R&D departments is your canary in the coal mine—stay away.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The Product Test",
                                description: "Never invest in a B2C company unless you have personally tested the product or spoken to three people who use it daily.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 179,
                currentProgress: 0.0
            ),
            2: CoreChapterContent(
                chapterNumber: 2,
                chapterTitle: "Auditing the Engine (Sales & Innovation)",
                bookTitle: "Common Stocks and Uncommon Profits",
                bookAuthor: "Philip Fisher",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"Better Mousetrap\" Fallacy")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is the most expensive mistake you will ever make: assuming the best product wins. You find a tech company with faster chips, cleaner code, or a revolutionary drug. You pile in, convinced the world will beat a path to their door. One year later, the stock is flat, and a competitor with inferior tech is eating their lunch. Why? You fell for the \"Engineer’s Delusion.\" You audited the engine (the innovation) but ignored the transmission (the sales force). An engine without a transmission just makes noise; it doesn't move the car.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Invisible Army")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Philip Fisher refused to invest in \"lab experiments.\" He tells the story of visiting companies where the R&D department was treated like royalty—pristine labs, unlimited budgets, geniuses in white coats. But when he asked about the sales organization, the executives would wave it off as a necessary evil. Fisher would immediately walk away.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He knew that without a ruthless, efficient mechanism to educate the customer, innovation is just an expensive hobby. He looked for companies that treated their salespeople with the same reverence as their scientists. Fisher realized that a \"profit squeeze\" rarely comes from high manufacturing costs; it comes from an inefficient sales team that burns cash trying to find customers. The \"Common Stock\" winner wasn't just an inventor; it was a master storyteller.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Distribution Moat")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the digital age, the most effective salesperson often isn’t a human in a suit; it’s the code itself. Fisher’s obsession with the \"sales organization\" has evolved into what we now call Product-Led Growth (PLG). Look at the battle between Slack and HipChat. HipChat had the first-mover advantage, but Slack didn't just build a chat app; they engineered a viral sales loop directly into the user experience. Fisher would tell you today that if a user needs a manual or a sales call just to start using the software, the engine is already stalling. The product must sell itself before the sales team even picks up the phone.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("This distinction is currently deciding the winners in the AI gold rush. Right now, thousands of startups are wrapping the exact same underlying models (LLMs) and claiming they have a revolutionary engine. They don't. The moat today isn't the algorithm, which is rapidly becoming a commodity; it’s the workflow and the community. A crypto project might have groundbreaking sharding technology, but if their Discord server is a ghost town or their documentation is impenetrable, the 'engine' is worthless. You must look for companies that obsess over the integration of their tech into daily life, not just the tech itself.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Careers\" Audit",
                                description: "Go to the company's \"Hiring\" page right now. Are they hiring Enterprise Sales Reps and Customer Success Managers? If they are only hiring engineers, they are building a lab, not a business. Avoid them.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The \"Docs\" Test",
                                description: "For tech companies, look at their developer documentation. If it is confusing, outdated, or hard to read, their \"sales engine\" is broken. Friction there means friction in revenue.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 205,
                currentProgress: 0.0
            ),
            3: CoreChapterContent(
                chapterNumber: 3,
                chapterTitle: "Decoding Management DNA",
                bookTitle: "Common Stocks and Uncommon Profits",
                bookAuthor: "Philip Fisher",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"Visionary\" Trap")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("You watch the keynote. The CEO strides across the stage in a leather jacket, promising to revolutionize the industry with a single algorithm. You are hooked. You buy the stock. Six months later, you are down 40%. Why? Because you bought the sales pitch, not the operator. You fell for the \"Charisma Trap.\" Most investors fail here because they assess a captain only when the sea is calm. But a CEO’s true DNA isn't revealed in the CNBC interview when the stock is at an all-time high; it is revealed in the silence that follows a disaster.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"Clam\" Test")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Philip Fisher had a ruthless litmus test for management: The \"Clam\" Factor. He tells the story of management teams that were the darlings of Wall Street—friendly, accessible, and constantly issuing press releases—as long as the charts were green. But the moment a factory burned down or a product failed, the phone lines went dead. They \"clammed up.\" Fisher instantly sold these stocks.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He realized that a management team that hides from its shareholders during a crisis lacks the fundamental integrity to build long-term wealth. He hunted for the rare leaders who were more talkative when losing money than when making it. For Fisher, candor in a crisis was the only true indicator of a company’s survival instinct.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Era of the \"Twitter Meltdown\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the modern era, the \"Clam\" has evolved into the \"Deflector.\" We live in the age of the Cult CEO, where leaders often use social media to bypass scrutiny. Look at the crypto collapse of FTX. Investors were blinded by the effective altruism and the media persona, missing the complete lack of internal controls. The red flag wasn't silence; it was the noise of distraction.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, you must analyze how a CEO handles \"technical debt\" and remote culture. When a tech giant misses a quarter, does the CEO take the blame for over-hiring, or do they blame \"lazy remote workers\" and demand a return to the office? The latter is a modern \"Clam\"—a leader refusing to own their strategic failure. True \"Management DNA\" in the 2020s looks like Jensen Huang at NVIDIA or Satya Nadella at Microsoft, who dissect their own failures publicly before the market can do it for them. They treat the shareholders as partners, not an audience to be managed.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Bad News\" Audit",
                                description: "Find the last time the company's stock dropped 10% in a single day. Find the CEO's statement or tweet from that exact day. If they blamed \"macroeconomics\" or the \"Fed\" instead of their own execution, sell the stock.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The \"Insider\" Divergence",
                                description: "Check the insider trading logs. If the CEO is talking about \"record growth\" on TV, but the CFO (Chief Financial Officer) is quietly selling shares, trust the CFO’s fear over the CEO’s optimism.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 190,
                currentProgress: 0.0
            ),
            4: CoreChapterContent(
                chapterNumber: 4,
                chapterTitle: "Sniper Entries & The Myth of Market Timing",
                bookTitle: "Common Stocks and Uncommon Profits",
                bookAuthor: "Philip Fisher",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"Limit Order\" Tragedy")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is how you lose a fortune while trying to save lunch money. You find the perfect company. You’ve done the \"Scuttlebutt.\" You know it’s the next big thing. The stock is trading at $50.50. You, trying to be a \"smart\" trader, put in a limit order at $50.00. You want that psychological win of getting a bargain. The stock dips to $50.05, then turns around and runs to $500. You missed a 1,000% return because you were haggling over 50 cents. Most investors fail not because they pick the wrong stock, but because they treat a generational asset like a used car, refusing to buy unless the sticker price is slashed.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"Eighths and Quarters\" Trap")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Philip Fisher saw this destroy more wealth than any bear market. He tells the story of an investor who identified a small, high-growth manufacturing company. The stock was trading around $35.50. The investor decided the \"fair value\" was exactly $35.00. He refused to pay the extra 50 cents—back then, stocks traded in \"eighths and quarters.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He placed his limit order and waited. The stock hovered, touched $35.25, and then the earnings report came out. It skyrocketed. Decades later, that company had split multiple times and was worth millions. The investor had been \"right\" about everything—the product, the management, the growth—except the price. Fisher’s rule became absolute: If the company is truly a \"Common Stock\" compounder, the current price is almost irrelevant in the long run. Buying \"at the market\" is the only way to ensure you actually get a seat on the rocket.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("Algorithm Food")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In Fisher’s day, you were competing with other humans. Today, you are competing with High-Frequency Trading (HFT) algorithms that can see your limit order and front-run it. If you are trying to \"time\" an entry into a volatile asset like Bitcoin or a high-beta AI stock like NVIDIA, you are playing a losing game. These assets don't move in straight lines; they move in violent steps. If you wait for the \"perfect pullback\" in a parabolic trend (like the AI boom), you will likely be left on the platform. The modern market moves too fast for \"sniper\" precision on price. The only precision that matters is \"sniper\" precision on quality.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Market Order\" Rule",
                                description: "If you have high conviction (Core 1, 2, & 3 are met), never use a Limit Order to try and save 1-2%. Hit \"Market Buy.\" The risk of missing the boat is mathematically higher than the risk of overpaying by 2%.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The \"Glitch\" Entry",
                                description: "Set alerts for \"non-fundamental\" crashes. If a solid company drops 15% because of a broader market panic (like a generic \"inflation scare\") or a temporary technical outage, that is your only valid \"timing\" signal. Buy the panic, ignore the price.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 191,
                currentProgress: 0.0
            ),
            5: CoreChapterContent(
                chapterNumber: 5,
                chapterTitle: "The Art of the 'Forever Hold'",
                bookTitle: "Common Stocks and Uncommon Profits",
                bookAuthor: "Philip Fisher",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"Taking Profits\" Fallacy")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("This is the most painful lesson you will ever learn: you will likely make more money from the one stock you didn't sell than from all the brilliant trades you did. The amateur investor feels an itch when a stock goes up 50%. They think, \"I should take some chips off the table.\" They sell the winner and put that money into a \"cheaper\" stock that hasn't moved yet. This is called \"watering the weeds and cutting the flowers.\" You are systematically killing your compounders to feed your losers.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Accidental Billionaire")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Philip Fisher noticed something strange about the wealthiest families he advised. They didn't get rich by being smart traders; they got rich because they were \"trapped.\" In Fisher's time, capital gains taxes were incredibly high—sometimes over 25-30% on paper profits. He tells the story of investors who bought a great company, watched it double, and wanted to sell. But when they calculated the tax bill, they realized they would lose a third of their wealth to the IRS. So, they gritted their teeth and held.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Ten years later, that \"forced holding\" had turned a double into a 50-bagger. The tax code had accidentally immunized them against their own stupidity. Fisher realized that the only time to sell a company is when the business changes (Core 1, 2, or 3 breaks), never because the price has gone up. If the job of the company is to grow, your job is to do nothing.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Volatility Tax")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the crypto and tech era, the \"tax\" isn't just from the IRS; it's the volatility. Look at Amazon or Bitcoin. To capture the 10,000% gains, you had to sit through multiple drawdowns of 50%, 70%, even 90%. If you sold Bitcoin in 2013 because it \"doubled too fast,\" you missed the defining asset class of the decade. The same applies to early Tesla investors. The \"Forever Hold\" isn't about stubbornness; it's about recognizing that true disruption takes decades, not quarters, to play out. If you own an ETF like QQQ, you are effectively outsourcing this \"holding\" discipline—the index automatically holds the winners and drops the losers. But if you are picking individual stocks, you must be the algorithm that refuses to sell just because the ride gets bumpy.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Thesis Check\"",
                                description: "Before you hit sell, write down why. If your reason is \"it's gone up too much\" or \"I want to buy a car,\" stop. You are cutting a flower.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The \"Broken Gear\" Rule",
                                description: "The only valid sell signal is if the \"Engine\" (Core 2) or the \"Management\" (Core 3) is broken. If the CEO leaves or the product stops growing, sell immediately, regardless of the price.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 184,
                currentProgress: 0.0
            ),
            6: CoreChapterContent(
                chapterNumber: 6,
                chapterTitle: "Immunizing Against Noise",
                bookTitle: "Common Stocks and Uncommon Profits",
                bookAuthor: "Philip Fisher",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"Macro\" Illusion")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is why you panic-sold at the bottom: You listened to a \"Macro Tourist.\" You let a stranger on television, who has never analyzed the specific company you own, convince you that the entire economy is collapsing. You looked at the \"market\" instead of the business. You obsessed over interest rates, election polls, and GDP forecasts—variables you cannot control and definitely cannot predict. Most investors fail because they treat the stock market like a voting machine, trying to guess what other people will do, rather than a weighing machine that measures what the company is doing.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"Dividend\" Trap")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Philip Fisher saw investors constantly seduced by the wrong metrics. One of his most famous \"Don'ts\" was: \"Don't buy a stock just because the annual report has a nice tone.\" But his deeper lesson was about ignoring the crowd’s obsession with \"safety.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He tells the story of investors who would pass on a high-growth technology company because it didn’t pay a dividend. They wanted the \"safe,\" tangible check in the mail. They flocked to stagnant companies that paid 5% yields but whose stock price slowly bled to death over decades. Fisher realized that \"income\" is often a mask for a dying business. A company that pays out all its profits has admitted it has no more ideas on how to grow. The \"safe\" crowd was actually taking the biggest risk of all: the risk of obsolescence. He taught that true safety isn't in a dividend check; it's in a profit margin that is expanding.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Algorithm of Fear")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, the noise isn't just a quarterly report; it’s a 24/7 algorithmic assault. If you open Twitter/X or turn on CNBC, you are bombarded with \"Recession Imminent\" or \"Crypto is Dead\" headlines. These platforms are designed to maximize engagement, and fear drives engagement.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The \"Fed\" Obsession: In 2023, every \"expert\" predicted a recession. If you sold your tech stocks (like Meta or NVIDIA) because you were scared of the Fed raising rates, you missed one of the greatest rallies in history.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The \"Narrative\" vs. Numbers: Look at Netflix in 2022. The narrative was \"streaming is over.\" The stock crashed. But the numbers (subscribers, revenue per user) showed the engine was still running. If you listened to the noise, you sold. If you looked at the \"Engine\" (Core 2), you bought.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"mute\" Protocol",
                                description: "Go to your social media. Mute or unfollow every account that posts about \"macroeconomics,\" \"The Fed,\" or \"Market Crashes.\" If they don't analyze specific companies, they are noise.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The \"Quarterly\" Rule",
                                description: "Only check your stock prices once every 3 months, right after the earnings report. If the company hit its numbers, close the app. If the price is down but the numbers are up, buy more.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 187,
                currentProgress: 0.0
            ),
        ],
        6: [
            1: CoreChapterContent(
                chapterNumber: 1,
                chapterTitle: "Escaping the Intermediary Trap",
                bookTitle: "The Little Book of Common Sense Investing",
                bookAuthor: "John C. Bogle",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The House Always Wins")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Listen closely. The reason most intelligent people fail at building wealth isn’t that they pick the wrong stocks. It’s that they are playing the wrong game entirely. You’ve been trained to believe that \"investing\" means outsmarting the person on the other side of the screen—finding that hidden gem before the crowd does.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But here is the dirty secret the finance industry spends billions to hide: The stock market is a closed system. It’s a giant pie. If you take a bigger slice, someone else must take a smaller one. But standing between you and that pie is a massive industry of croupiers, dealers, and fixers. They don't care if the pie grows or shrinks, as long as they get their commission for cutting the slices. Before we even talk about strategy, we have to stop the bleeding. You aren't losing to the market; you are losing to the \"helpers.\"")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Parable: The Gotrocks Family")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In our source material, the author illustrates this with a brilliant metaphor: The Parable of the Gotrocks.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Imagine a wealthy family, the Gotrocks, who own 100% of the stock market. Every year, they reap the rewards of corporate America—dividends and earnings growth. They grow wealthier simply by sitting still. But then, a few smooth-talking \"Helpers\" (brokers) arrive. They convince cousin Jimmy that he can earn more if he sells his shares to cousin Sally. The Helpers take a fee for the transaction.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Soon, the whole family is frantically swapping shares, hiring \"Managers\" to pick the best shares and \"Consultants\" to pick the best Managers. The result? The family’s total wealth begins to shrink. They are still owning the same companies, but now they are paying 20% of their profits to the Helpers. The lesson is ruthless in its simplicity: In investing, you get what you don't pay for. The Helpers add zero value; they subtract it.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Modern Trap: The Gamification of Speculation")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, the \"Helpers\" don't just look like guys in suits on Wall Street; they look like fun, colorful apps on your phone.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The modern version of the Gotrocks tragedy is happening right now in sectors like Crypto, AI, or Clean Energy. You see a \"commission-free\" trading app and think you’ve beaten the system. You haven't. When you trade the latest hot AI stock or chase a meme coin, you are generating data (Payment for Order Flow) that the app sells to high-frequency traders. You are the product.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Every time you click \"trade\" to chase a cybersecurity trend or dump a stock because of a Trade War headline, you are effectively paying a toll. The friction has moved from expensive broker fees to invisible spreads and behavioral nudges designed to make you trade more. The \"Helpers\" have just digitized their skimming operation.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "Calculate the Croupier's Cut",
                                description: "Log into your current accounts. Locate the \"Expense Ratio\" for every fund you own. If any number is above 0.5%, you are actively donating your future wealth to a Helper. Mark it for consideration more.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "Delete the Casino",
                                description: "If you have a trading app on your phone that sends you push notifications when stocks move, be careful! You cannot win a game that is designed to make you transact.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 217,
                currentProgress: 0.0
            ),
            2: CoreChapterContent(
                chapterNumber: 2,
                chapterTitle: "Separating Business from Speculation",
                bookTitle: "The Little Book of Common Sense Investing",
                bookAuthor: "John C. Bogle",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Optical Illusion of Wealth")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("There is a dangerous optical illusion that blinds almost everyone who opens a brokerage app. You see a green line going up, and you think you are \"winning.\" You see a red line going down, and you think you are \"losing.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("This is the friction that destroys portfolios. You have been conditioned to believe that the price of a stock is the same thing as the value of the business. They are not. In fact, in the short term, they are often total opposites. Most investors fail because they are playing a game of psychological warfare against millions of strangers, trying to guess what they will pay for a stock tomorrow, rather than focusing on what the company actually does.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Story: The Tale of Two Games")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author, channeling the wisdom of his mentor, tells us that the stock market is actually two separate games played on the same field.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Game One is \"The Real Market.\" This is the boring, slow game where companies—let’s say, a beverage giant or a manufacturer—sell products, pay rent, and distribute their profits to you as dividends. This game is driven by hard math: earnings growth and dividend yields. It is the \"Weighing Machine.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Game Two is \"The Expectations Market.\" This is the loud, frantic game where investors bet on how much other investors will pay for those earnings in the future. This is the \"Voting Machine.\"  The author illustrates this with the historic bubbles of the past: companies whose earnings (The Real Market) were solid, but whose stock prices (The Expectations Market) detached from reality, soared into the stratosphere, and inevitably crashed back to earth. The lesson? The \"Real Market\" always wins in the end. Speculation is just noise that cancels itself out over time.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Modern Context: The AI Hall of Mirrors")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, this distinction is more critical—and harder to see—than ever. Look at the current explosion in Artificial Intelligence and Data Centers.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The \"Real Market\" for AI is tangible: companies are building massive server farms, laying fiber optic cables, and selling software. There is real earnings growth here. But the \"Expectations Market\" has turned this into a casino. You see obscure tech stocks doubling overnight not because their profits doubled, but because the narrative changed.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("This applies perfectly to the crypto ecosystem or the \"clean energy\" boom. Often, the business of clean energy is growing steadily (solar panel installations are up), but the stocks are volatile wrecks because speculators piled in, drove the P/E ratios to the moon, and then fled. If you cannot distinguish the business (the solar panel sales) from the speculation (the stock price), you will panic when the price drops, even if the business is healthier than ever.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The Moat Test",
                                description: "Before you believe a hype cycle, run a Deep Research Report using the Warren Buffett Persona. If the AI analysis cannot find a durable competitive advantage, you are gambling. Be careful!",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 200,
                currentProgress: 0.0
            ),
            3: CoreChapterContent(
                chapterNumber: 3,
                chapterTitle: "Accepting the Zero-Sum Game",
                bookTitle: "The Little Book of Common Sense Investing",
                bookAuthor: "John C. Bogle",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Alpha Delusion")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is the hardest pill to swallow. You have been told that if you study hard enough, read enough charts, and listen to enough podcasts, you can consistently beat the market. You believe that you can be the \"Alpha\"—the one who wins while others lose.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But the market is not a classroom where everyone can get an \"A\" if they study. It is a poker table. For you to win a dollar of \"excess\" return, someone else must lose a dollar. And that \"someone else\" isn't a clueless amateur anymore. It is a Goldman Sachs algorithm, a sovereign wealth fund, or a Nobel Laureate. When you click \"buy\" on a hunch, that means someone else is selling, right? You have to ask: \"Who is on the other side of this trade, and why do they think I’m wrong?\"")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Story: The Relentless Arithmetic")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author strips away the magic and leaves us with cold, hard arithmetic. He presents the \"Cost Matters Hypothesis.\" Imagine the market returns 10% this year. All investors, as a group, earn 10% before costs. But the financial system—the croupiers, the managers, the brokers—charges 2% to play the game.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("This means the average investor only takes home 8%. For you to get 12%, someone else must get 6%. It is a mathematical certainty. The author describes the industry as a \"Giant Scam\" where the managers convince you they can find the winning stocks. But the data shows that over 15 or 20 years, almost no manager consistently wins. They are just flipping coins, and eventually, their luck runs out. The only winner is the house. The author acknowledges that yes, there are \"stars\"—the Warren Buffetts and Peter Lynchs of the world. But here is the catch: For every Buffett, there are a thousand managers who thought they were Buffett and failed.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Modern Context: The Rise of the Machine")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the author's day, you were competing against men in trading pits. Today, you are competing against silicon.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the modern context, \"Alpha\" has been eaten by code. High-frequency trading (HFT) firms use microwave towers to execute trades in microseconds, front-running news before it even hits your screen. In the crypto markets, \"MEV bots\" (Maximal Extractable Value) automatically drain value from retail traders. Even in the \"Value\" space, AI agents are scanning 10-Ks faster than you can open the file.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("When you try to trade in and out of hot sectors like AI or Semiconductors, you are bringing a knife to a nuclear gunfight. The \"Smart Money\" isn't just smart; it's faster, richer, and more ruthless than you. The only way to win a rigged game is to refuse to play by their rules.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Whale\" Reality Check",
                                description: "Go to the Whale Tracking tab  in your app. Look for a stock where one major fund (e.g., a Hedge Fund) is buying and another is selling. Witness the zero-sum game in real-time: two \"geniuses\" betting against each other. Realize that if they can't agree, your odds of outsmarting them are near zero.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "Diversify Your Survival",
                                description: "Check your Portfolio Insights widget for your Diversification Score. If the score warns that you are heavily concentrated in one sector (e.g., \"90% Tech\"), you are trying to beat the dealer. Rebalance until the score indicates you own the \"House\" (the broad economy), not just a few risky tables.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 227,
                currentProgress: 0.0
            ),
            4: CoreChapterContent(
                chapterNumber: 4,
                chapterTitle: "Defeating the Tyranny of Compounding Costs",
                bookTitle: "The Little Book of Common Sense Investing",
                bookAuthor: "John C. Bogle",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Silent Heist")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("There is a thief in your portfolio, and he is invisible. You spend hours researching whether \"Clean Energy\" or \"AI\" will boom next year, agonizing over getting a 10% return versus an 8% return. But while you are distracted by the upside, the \"Silent Heist\" is happening on the downside.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The financial industry has mastered the art of presenting fees as \"small numbers.\" They say, \"It’s only 2%.\" You think, \"2% is nothing. I’ll still keep 98%.\" This is the single most expensive math error a human being can make. That \"tiny\" fee is not a one-time toll; it is a recurring cancer that metastasizes over time, consuming the majority of your wealth before you even retire.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Story: The Fourth Quartile Tragedy")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author illustrates this with a terrifying bit of \"Humble Arithmetic.\" Imagine you invest a nest egg over an investment lifetime (about 50 years). The market is generous, giving you a steady 7% or 8% annual return.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In Scenario A, you pay minimal costs. The \"Miracle of Compounding Returns\" works entirely for you.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In Scenario B, you pay a \"modest\" 2.5% in total costs (management fees, transaction costs, and sales loads).")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The result? You don't lose 2.5% of your fortune. You lose nearly 65% of it. Yes, you heard that right, nearly 65%! The financial system—the \"croupiers\"—ends up taking two-thirds of the pie, leaving you, the one who took all the risk and put up all the capital, with the crumbs. The author’s lesson is stark: In investing, you get what you don't pay for. Costs are the only variable you can control with 100% certainty.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Modern Context: The \"Thematic\" Trap")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the modern era, the \"2% Manager\" has been replaced by the \"Thematic ETF.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Wall Street knows you want to invest in \"The Future\"—AI, Data Centers, Cybersecurity, or Remote Work. So, they package these exciting concepts into niche ETFs. Unlike a boring S&P 500 fund (which might charge 0.03%), these \"specialized\" funds often carry Expense Ratios of 0.75% or higher.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Furthermore, in the world of Crypto, the costs are hidden in the \"Gas\" and the \"Spread.\" You might buy a meme coin thinking it's free, but the bid-ask spread (the difference between the buy and sell price) might be 1% instantly. If you trade in and out of that coin ten times a year, you have voluntarily paid a 10% tax on your own money. The modern \"Silent Heist\" isn't a check you write to a broker; it is the friction of hyper-activity in a gamified app.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Forever\" Filter",
                                description: "Go to the Research Tab and generate a report using the Warren Buffett Persona. Ask the AI specifically: \"Is this a company I can hold for 10 years without selling?\" If the answer is no, think again. The cheapest trade is the one you never have to exit.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 195,
                currentProgress: 0.0
            ),
            5: CoreChapterContent(
                chapterNumber: 5,
                chapterTitle: "Plugging the Tax Leak",
                bookTitle: "The Little Book of Common Sense Investing",
                bookAuthor: "John C. Bogle",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Government’s Invisible Equity")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("There is a silent partner in your portfolio who contributes zero capital, takes zero risk, yet claims up to 37% of your profits. You know him as Uncle Sam. The friction here is a dangerous mental accounting error: you see a stock go up 20% and think you have made 20%. You haven't.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Most investors treat taxes as an afterthought, something to worry about in April. But in the mathematics of wealth, taxes are a compound interest penalty. Every time you sell a winner to buy a new stock, you trigger a \"taxable event.\" You are voluntarily severing the arm of your compound interest curve. You aren't just paying a bill; you are removing capital that could have been working for you for the next 30 years.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Story: The Tale of the \"Statue\" vs. The Trader")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author illuminates this with a concept we can call the \"Tax Efficiency Gap.\" He compares two investors.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Investor A owns an actively managed mutual fund. The manager is frantic, constantly selling \"overvalued\" stocks to buy \"undervalued\" ones. This fund has a turnover rate of 100%—meaning the entire portfolio is replaced every year. This generates massive short-term capital gains distributions, which are taxed at the highest income rates. Investor A is bleeding money every year, even if he never sells a single share of the fund itself.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Investor B owns a broad market index fund. The fund manager does... nothing. He is a statue. He only sells if a company goes bankrupt or leaves the index. Because there is almost zero turnover, there are almost zero capital gains taxes. Investor B’s money compounds tax-deferred, growing like an IRA, simply because the vehicle is efficient. The lesson? In investing, laziness is tax-efficient; activity is tax-punitive.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Modern Context: The Crypto & ETF \"Shell Game\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, the tax leak has turned into a flood, driven by the gamification of \"swapping.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Consider the modern Crypto investor. You swap Ethereum for Solana, then Solana for a \"Memecoin,\" then back to a Stablecoin. In your mind, you are just moving chips on a table. In the eyes of the IRS, every single swap is a sale and a purchase. You might trigger five taxable events in an hour. You could easily owe more in taxes than you have in cash if the market crashes after your trades.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The same applies to the \"Sector Rotation\" game in stocks. You sell your \"Clean Energy\" ETF because it's down to buy a \"Data Center\" ETF because it's hot. You are converting potential long-term capital gains (taxed at ~15-20%) into short-term gains (taxed at ~37%). You are sprinting up an escalator that is moving down.")
                    ),
                ],
                audioDurationSeconds: 181,
                currentProgress: 0.0
            ),
            6: CoreChapterContent(
                chapterNumber: 6,
                chapterTitle: "Ignoring the Siren Song of \"Stars\"",
                bookTitle: "The Little Book of Common Sense Investing",
                bookAuthor: "John C. Bogle",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Rearview Mirror Trap")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("There is a fatal flaw in human psychology that the financial industry exploits with ruthless efficiency: We believe that what just happened is what will happen. When you see a fund with a \"5-Star Rating\" or a \"Top Performer 2024\" badge, your brain screams \"Safety!\" and \"Excellence!\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But in finance, the rearview mirror is broken. Buying a fund because it performed well last year is like betting on a roulette number because it just came up three times in a row. You are not buying \"excellence\"; you are buying \"peak valuation.\" You are arriving at the party exactly when the police are banging on the front door. The friction here is that you are chasing heat, but by the time you feel the warmth, the fire is already burning out.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Story: The Legend of the \"Comet\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author warns us to distinguish between \"Stars\" (permanent fixtures) and \"Comets\" (balls of gas that burn out). He tells the story of the \"Go-Go Years\" of the 1960s. There was a manager named Gerald Tsai who launched the Manhattan Fund. He was the \"financial wizard\" of his day, the genius who could do no wrong. His fund shot up like a rocket, attracting millions of dollars from investors desperate to get in on the magic.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Then, gravity took over. The \"Reversion to the Mean\" kicked in. The Manhattan Fund didn't just underperform; it collapsed, losing 90% of its assets over the next few decades. The author uses this tragedy to illustrate a statistical certainty: \"Yesterday’s winners are tomorrow’s losers.\" The 5-star rating you see today is almost statistically guaranteed to become a 3-star or 1-star rating within five years. The \"Star\" system doesn't predict the future; it merely records the past.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Modern Context: The \"Disruptor\" Delusion")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The \"Manhattan Fund\" has been reincarnated as the \"Disruptive Innovation\" ETF.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Think of the recent obsession with \"star\" managers or the explosion of thematic ETFs focusing on \"AI Robotics\" or \"Crypto Miners.\" These funds post astronomical returns for one or two years. They appear on every CNBC segment. Influencers on Twitter crown the managers as visionaries.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But look closer. These funds often concentrate their bets on high-risk, unprofitable tech stocks. When interest rates rise or the hype cycle breaks (as we saw in 2022), the \"Disruptor\" funds crash harder than the market. If you bought in after the 5-star year—which is what 90% of retail investors do—you didn't buy the disruption; you paid for the bag. The \"Siren Song\" today is amplified by algorithms that feed you news about whoever is winning right now, blinding you to the cliff edge they are approaching.")
                    ),
                ],
                audioDurationSeconds: 180,
                currentProgress: 0.0
            ),
            7: CoreChapterContent(
                chapterNumber: 7,
                chapterTitle: "Buying the Haystack",
                bookTitle: "The Little Book of Common Sense Investing",
                bookAuthor: "John C. Bogle",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Treasure Hunter’s Curse")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("We are all born with a fatal flaw in our financial DNA: the belief that we are smarter than the average. The financial industry knows this. They treat the stock market like a vast, sandy beach and sell you a metal detector. They whisper that if you just study the charts hard enough, listen to the right podcasts, or subscribe to the premium newsletter, you will find the buried gold. You spend your days agonizing over whether to back \"Quantum Computing\" or \"Gene Editing,\" convinced that your research will reveal the next Amazon before the rest of the world catches on.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The friction here is mathematical, and it is brutal. The stock market is not a beach full of gold; it is a graveyard of mediocrity dotted with a few spectacular supernovas. For every Apple, there are a thousand bankrupt startups. For every Bitcoin, there are ten thousand dead altcoins. Most investors fail because they exhaust themselves trying to find the needle, never realizing that the farm itself was for sale. They take on the risk of the needle (total loss) without ever guaranteeing the reward.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Story: The Laziest Man in the Room")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author, John Bogle, solved this problem with a metaphor so simple it insulted the entire financial establishment: \"Don't look for the needle in the haystack. Just buy the haystack!\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He tells the story of the \"Laziest Man in the Room.\" Imagine two investors. The first is the \"Picker.\" He is frantic. He reads annual reports until 2:00 AM, pays huge fees to managers, and constantly swaps one stock for another, trying to guess which company will survive. The second is the \"Haystack Owner.\" He is boring. He buys a single fund that owns a tiny slice of every public company in America. He goes to sleep.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Over 50 years, a strange thing happens. The Picker misses the one or two stocks that drive all the market's returns—maybe he sold them too early, or maybe he never bought them at all. But the Haystack Owner? He owned the winners the entire time. He didn't have to find them; he simply couldn't miss them. By owning the entire market, he guaranteed that he would capture 100% of the innovation and growth of the corporate economy. He defeated the \"smart\" money by refusing to play their guessing game.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Modern Context: The \"Magnificent Seven\" Paradox")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, the \"Haystack\" strategy is not just a safety net; it is the only logical offense. The market has become incredibly top-heavy, dominated by what we now call the \"Magnificent Seven\" (companies like NVIDIA, Microsoft, Apple, etc.).")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the last few years, if you tried to be clever and pick \"undervalued\" small-cap stocks, or if you bet heavily on a specific niche like \"Solar Energy\" or \"Electric Vehicles,\" you likely lost money. Why? Because the market's entire return was driven by just those seven massive AI-adjacent companies. If you didn't own them, you didn't win.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("This applies perfectly to the current \"AI Super Cycle.\" You might be tempted to pick the specific startup making the cooling fans for data centers. That is a gamble. But if you buy the Total Market Index (the Haystack), you automatically own the chip designer (NVIDIA), the cloud provider (Microsoft), and the energy company powering the grid. You capitalize on the trend of AI without betting your life savings on which specific CEO will win the war. You turn the chaotic casino of \"stock picking\" into the reliable utility of \"capitalism capturing.\"")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Haystack\" Audit",
                                description: "Go to your Tracking Tab and look at the Diversification Score widget. The Instruction: If your score is low (e.g., heavily weighted in \"Tech\" or \"Crypto\"), you are holding a handful of grass, not the haystack. You are exposed to the risk of a single fire wiping you out. Aim for a score that reflects the broad economy.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 263,
                currentProgress: 0.0
            ),
            8: CoreChapterContent(
                chapterNumber: 8,
                chapterTitle: "Respecting the Law of Gravity",
                bookTitle: "The Little Book of Common Sense Investing",
                bookAuthor: "John C. Bogle",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Icarus Delusion")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("There is a seduction in the stock market that destroys more wealth than any recession: the Parabola. You see a stock chart that looks like a vertical line—shooting straight to the moon—and your brain whispers, \"This is the new normal.\" You convince yourself that the rules have changed, that this company is so revolutionary it has escaped the laws of physics.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The friction here is emotional. We are hardwired to chase momentum. When a stock doubles in a month, we feel a primal urge to jump on board, terrified of missing out. But in finance, what goes up abnormally fast doesn't just come down; it crashes. You aren't buying growth; you are buying the moment before the rubber band snaps. Most people fail because they mistake a temporary manic episode for a permanent shift in reality.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Story: The \"Nifty Fifty\" Massacre")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author, a historian of market folly, tells the story of the \"Nifty Fifty\" mania of the early 1970s. These were the \"AI darlings\" of their day—companies like Polaroid, Xerox, and Avon. They were considered \"one-decision\" stocks: buy them at any price and never sell. They traded at 80 or 90 times their earnings because investors believed their growth was infinite.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Then, the \"Iron Law of Reversion to the Mean\" (RTM) kicked in. Bogle explains RTM as the gravitational force of finance. Corporate returns always gravitate back to the average over time. The \"Nifty Fifty\" didn't just stop going up; they collapsed, losing 80% to 90% of their value. The companies were fine—Xerox didn't go out of business—but their valuations had detached from reality. The lesson? Trees do not grow to the sky. If a stock's price leaves its earnings behind, gravity will eventually pull it back down—usually with violence.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Modern Context: The \"Super Cycle\" Trap")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("Now, the \"Nifty Fifty\" has been replaced by the \"AI Super Cycle.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Look at the recent explosion in Data Center stocks or the mania around specific \"AI Hardware\" manufacturers. You hear the exact same \"New Paradigm\" arguments: \"This time is different because of Generative AI!\" or \"Crypto is the future of money, so price doesn't matter!\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("We saw this recently with stocks like Super Micro Computer (SMCI) or various \"Altcoins.\" They went vertical, creating instant millionaires on paper. Then, RTM arrived. One bad earnings report, one regulatory headline, and the stock fell 50% in weeks. The \"Law of Gravity\" applies to everyone—even the company building the future. If the P/E ratio is expanding faster than the profits, you are essentially defying gravity without a parachute. When you buy into a \"Trade War\" beneficiary or a \"Remote Work\" darling after it has already tripled, you are not investing; you are volunteering to be the bag holder for the smart money that is already exiting.")
                    ),
                ],
                audioDurationSeconds: 189,
                currentProgress: 0.0
            ),
            9: CoreChapterContent(
                chapterNumber: 9,
                chapterTitle: "Anchoring with Bonds",
                bookTitle: "The Little Book of Common Sense Investing",
                bookAuthor: "John C. Bogle",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"All-Gas\" Suicide Pact")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("There is a dangerous allergy in the modern investor's mind: an allergy to \"boring.\" You look at your portfolio and think, \"Why would I lend money to the government for a 4% yield when I could own NVIDIA or Bitcoin and make 40%?\" This logic seems flawless in a bull market. You view every dollar not in high-growth stocks as \"wasted capital.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The friction here is that you are building a race car with a massive engine and absolutely no brakes. You are optimizing for speed, but you are ignoring survivability. Most investors fail not because they don't pick winners, but because they get shaken out of their winners. When the market drops 30%—and it will—the \"All-Gas\" investor panics and sells at the bottom to stop the pain. You don't need bonds to make you rich; you need them to keep you sane when the world is going crazy.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Story: The Anchor to Windward")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author, John Bogle, often used nautical metaphors to explain this unsexy truth. He viewed the stock market as a turbulent ocean. Stocks are the sails; they capture the wind and drive your wealth forward. But the wind is unpredictable. Sometimes it turns into a hurricane.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Bogle argued that Bonds act as the \"Anchor to Windward\" (or the ballast in the ship). When the hurricane hits and stock prices are crashing, bonds usually hold their value or even go up (as investors flee to safety). He tells the story of the \"Balanced Investor.\" While the aggressive \"Growth Investor\" is watching his net worth get cut in half during a crash—and is likely puking up his shares in a panic—the Balanced Investor looks at his bonds. They are steady. They offer a psychological cushion that allows him to say, \"I am down, but I am not out.\" The bonds don't drive the return; they ensure you survive the journey long enough to get the return.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Modern Context: The \"Digital Gold\" Fallacy")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the modern era, the \"Anchor\" has been confusingly swapped for \"Digital Gold\" or \"Yield Farming.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Many young investors today believe that Bitcoin or Ethereum is their \"hedge\" against the system. They think, \"If the stock market crashes, my Crypto will save me.\" But looking at the data from recent tech corrections (like 2022), we see the opposite: Crypto often correlates highly with speculative tech stocks. When AI and Data Center stocks crash, Crypto often crashes harder. That is not an anchor; that is just more sails.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Similarly, \"High Yield\" fintech savings accounts or \"Stablecoin\" lending protocols are not bonds. They carry platform risk and regulatory risk. True bonds (like U.S. Treasuries) are the only asset class that historically behaves differently than stocks during a recession. If your portfolio is 100% Tesla, NVIDIA, and Solana, you don't have a portfolio; you have a single, leveraged bet on \"Risk On\" sentiment. When the mood changes, you have nowhere to hide.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The Risk-Free Hurdle",
                                description: "Search for a Treasury ETF (like SGOV or BIL). Check the Dividend Yield. If this \"risk-free\" number is near 4-5%, ask yourself: \"Is my risky tech portfolio actually beating this guaranteed hurdle after the recent drop?\" If not, you are taking on stress for zero premium.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 218,
                currentProgress: 0.0
            ),
            10: CoreChapterContent(
                chapterNumber: 10,
                chapterTitle: "Navigating the ETF Minefield",
                bookTitle: "The Little Book of Common Sense Investing",
                bookAuthor: "John C. Bogle",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Weaponization of Access")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("There is a paradox in modern finance: You have never had more access, yet you have never been more likely to blow yourself up. In the old days, if you wanted to bet on \"Cybersecurity\" or \"Lithium Mining,\" you had to call a broker, pay a huge commission, and buy specific stocks. The friction saved you.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, the financial industry has removed the friction. They have turned the stock market into a candy store. There is an ETF (Exchange Traded Fund) for everything—\"Gen Z Consumption,\" \"Space Exploration,\" \"Obesity Drugs.\" You see a headline about a Trade War, and within seconds, you can buy a \"Deglobalization ETF.\" The trap is subtle: You think you are \"diversifying\" because you bought a fund. In reality, you are just speculating on narrow, volatile themes with a product that looks safe but behaves like dynamite. The industry has weaponized access, handing you a bazooka and telling you it’s a water gun.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Double-Edged Sword")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author, John Bogle, had a complicated relationship with the ETF. He actually invented the precursor to it—the index fund. But when the first ETFs launched (like the \"Spider\" or SPY), he was horrified. He called them a \"Purist's Nightmare.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He tells the story of the ETF as a tool that was corrupted by its own utility. Bogle compared the traditional index fund to a \"reliable family sedan\"—boring, safe, and gets you to your destination (retirement). He compared the ETF to a \"sawed-off shotgun.\" It is a powerful tool if you are a professional hunter (a trader), but if you leave it lying around the house, you are likely to blow your foot off.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("His warning was prophetic. He saw that because ETFs could be traded instantly like stocks, investors would inevitably treat them like stocks. Instead of buying the \"Haystack\" and holding it for 50 years, they would trade the \"Haystack\" at 10:00 AM and sell it at 2:00 PM. He argued that the ETF structure encouraged the worst behavioral sin in investing: the illusion that you can time the market.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Modern Context: The \"Thematic\" Shell Game")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the modern era, the \"Shotgun\" has evolved into a sniper rifle aimed at your wallet. The most dangerous trap today is the \"Thematic ETF.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Wall Street knows you are smart enough to avoid high-fee mutual funds. So, they repackage active management as \"Thematic ETFs.\" You see funds for \"AI & Robotics,\" \"Clean Energy,\" \"Work From Home,\" or \"Next Gen Internet.\" These funds charge 0.60% or 0.75%—ten times more than a standard S&P 500 ETF.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Take the \"AI Data Center\" trend. You might rush to buy a niche ETF that holds cooling system manufacturers and chip designers. But look closely at the holdings. The top three stocks are likely NVIDIA, Microsoft, and Amazon. If you already own a total market fund, you already own these companies. You are paying a \"Thematic Tax\" of 0.75% just to double-count the same stocks you already have in your cheap index fund. The industry has tricked you into paying active fees for passive products by appealing to your desire to be part of the \"next big thing.\"")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"Fee\" Audit (ETFDetailView)")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Search for any niche or thematic fund you are watching and locate the Net Yield in the Snapshots. The Instruction: If the number is above 0.20%, stop. Ask yourself: \"Am I paying this premium for better performance, or just for a cool name?\" Compare it to a standard Total Market ETF (usually 0.03%). If the gap is huge, the value proposition is likely zero.")
                    ),
                ],
                audioDurationSeconds: 240,
                currentProgress: 0.0
            ),
            11: CoreChapterContent(
                chapterNumber: 11,
                chapterTitle: "Avoiding the \"Smart Beta\" Trap",
                bookTitle: "The Little Book of Common Sense Investing",
                bookAuthor: "John C. Bogle",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Alchemy of Wall Street")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("There is a term in finance invented purely to make you feel inadequate: \"Smart Beta.\" It implies, rather rudely, that the standard index fund you own is \"Dumb Beta.\" The friction here is intellectual vanity. Wall Street knows you have accepted that stock-picking is hard, so they have pivoted to a new seduction: \"Don't pick stocks; pick the perfect algorithm.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("They sell you complex ETFs that claim to \"optimize\" the market. They promise to weight companies not by size, but by \"Quality,\" \"Momentum,\" or \"Volatility.\" It sounds like science. It looks like math. But in reality, it is marketing. It is the modern version of alchemy—trying to turn the lead of market averages into the gold of superior returns without taking on extra risk. Most investors fail here because they confuse a back-tested simulation with future reality.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Story: The \"Data Mining\" Mirage")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author, John Bogle, spent his later years battling this specific hydra. When \"Fundamental Indexing\" (the grandfather of Smart Beta) arrived, claiming it could beat the S&P 500 by simply weighting stocks differently, Bogle was the loudest skeptic in the room.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He tells the story of the \"Data Mining\" trap. If you look at the last 50 years of data, you can always find a pattern that beat the market. Maybe it was \"stocks that start with the letter A\" or \"companies with blue logos.\" If you build a fund around that past pattern, it looks like a genius strategy. Bogle warned that these \"Smart\" funds were just active management in a robot mask. They had higher turnover (taxes) and higher fees. He argued that \"Smart Beta\" is just a bet that a specific factor (like Value or Small Cap) will win forever. But markets adapt. The moment a \"secret anomaly\" is packaged into an ETF and sold to millions, the edge disappears. The only thing that remains is the fee.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Modern Context: The \"AI\" Black Box")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, \"Smart Beta\" has been given a sexy new makeover: Artificial Intelligence.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("You see ETFs now that claim to use \"AI and Machine Learning\" to rotate between sectors, or \"Sentiment Analysis\" to trade based on Twitter trends. You might see a \"Data Center Optimization\" ETF that claims to dynamically adjust its holdings based on electricity prices. This is the \"Smart Beta\" trap on steroids.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Consider the \"Quant\" strategies in Crypto or the complex \"Buffer ETFs.\" They are selling you a free lunch. But here is the irony: You already have AI. Your app gives you sentiment analysis and deep research. When you buy an \"AI ETF\" with a 0.75% expense ratio, you are often paying a middleman to do exactly what your phone can do for you. The author didn’t write about AI, but his logic cuts through the hype: If an algorithm truly had a guaranteed edge, the creator wouldn't sell it to you for a 0.75% fee; they would keep it secret and become a trillionaire!")
                    ),
                ],
                audioDurationSeconds: 200,
                currentProgress: 0.0
            ),
            12: CoreChapterContent(
                chapterNumber: 12,
                chapterTitle: "Auditing Your Advisor",
                bookTitle: "The Little Book of Common Sense Investing",
                bookAuthor: "John C. Bogle",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Most Expensive Friend You Have")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("We often hire financial advisors because we are scared. The stock market feels like a jungle, and we want a guide with a machete to hack through the vines of volatility. The friction is that this guide charges a toll—usually 1% of your assets per year—regardless of whether he leads you to safety or off a cliff.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Most people fail here because they view their advisor as a \"Market Beater.\" You hire them to find the best stocks or time the next crash. But the math says this is a fantasy. If an advisor could reliably beat the market by 2% a year, he wouldn't be managing your account for a 1% fee; he would be managing a hedge fund for billions. By hiring a human to do a job that a simple algorithm does better, you are often paying for a \"friend\" who slowly drains your retirement.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"Croupier\" vs. The Coach")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author, John Bogle, was ruthless about the \"Croupier\" class—the intermediaries who take a cut of every transaction. He argued that for 99% of investors, a stock-picking advisor is a net negative. The math of the \"Cost Matters Hypothesis\" proves that after fees, the average advised client must underperform the market.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("However, Bogle offered a nuanced solution. He didn't say never hire an advisor. He said hire them for the right reason. Do not pay for \"Investment Management\" (picking stocks). Pay for \"Wealth Management\" (asset allocation, tax planning, and estate planning). But most importantly, pay for \"Behavioral Coaching.\" The only value an advisor truly adds is standing between you and the \"Sell\" button during a panic. If your advisor is just an expensive indexer who charges you to own the market, fire them. But if they are a psychologist who stops you from making a fatal mistake, they are worth every penny.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Modern Context: The \"Finfluencer\" & The AI")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the modern world, the \"Advisor\" has mutated. You might not have a guy in a suit, but you have a \"Finfluencer\" on TikTok or YouTube screaming about \"The Next 100x Crypto\" or \"Why the Trade War Will Crash the Market.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("These digital advisors are even more dangerous because they are unregulated and algorithmically incentivized to terrify you. They don't charge a 1% fee; they charge you your attention and your sanity. They push you into high-fee \"Copy Trading\" platforms or pump-and-dump schemes.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("On the flip side, we now have \"Robo-Advisors\" and AI. The modern solution isn't necessarily a human; it is often a piece of software. An AI doesn't panic. It doesn't have an ego. It doesn't need to \"churn\" your account to prove it's working. Your own pocket device can now run the same \"Deep Research\" that a junior analyst at Goldman Sachs used to do. The democratization of data means the \"information edge\" your advisor claimed to have is gone.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"Jargon\" Detector")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("If an advisor suggests a complex product like an \"Indexed Annuity\" or a \"Structured Note,\" open the Chat Tab. The Instruction: Type: \"Explain the fees and downside caps of [Product Name] like I'm 12.\" The AI will strip away the sales language. If the product sounds like a trap after the translation, be careful!")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Second Opinion\" Protocol (Deep Research)",
                                description: "Next time your advisor (or a YouTube guru) pitches a stock, don't just nod. Open your Research tab and run a report using the Warren Buffett Persona. The Instruction: Compare the AI report's unbiased \"Cons\" list with the advisor's pitch. If the advisor glossed over the risks that the AI found in seconds, you know that Caudex has your back.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 245,
                currentProgress: 0.0
            ),
            13: CoreChapterContent(
                chapterNumber: 13,
                chapterTitle: "Mastering the Art of Doing Nothing",
                bookTitle: "The Little Book of Common Sense Investing",
                bookAuthor: "John C. Bogle",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Curse of the \"Do Something\" Instinct")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("There is a fatal flaw in human evolution that makes you a terrible investor: the biological bias for action. For two hundred thousand years, your ancestors survived because they were hyper-reactive. When a twig snapped in the jungle, the ones who paused to analyze the data were eaten; the ones who sprinted away survived to pass on their genes. You are the descendant of the paranoid and the twitchy.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the modern financial markets, this survival instinct is a suicide pact. When the S&P 500 drops 20% in a month, or when a headline screams about \"Hyperinflation,\" your amygdala—the lizard brain—hijacks your logic. It screams, \"Don't just stand there, do something! Sell! Hedge! Buy gold! Move to cash!\" You feel a physical, visceral guilt if you are not taking action to \"fix\" the problem.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The friction here is that investing is the only domain in human life where effort is negatively correlated with success. In your career, if you work harder, you make more money. In the gym, if you lift more, you get stronger. But in the stock market, the more you \"do\"—the more you trade, tinker, adjust, and react—the more likely you are to destroy your wealth. The \"insider secret\" that the financial industry spends billions to hide is that the most profitable activity you can engage in is often... absolutely nothing. The hardest thing to do in the world is to stand still while everyone around you is running for the exits.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Odysseus Contract")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author, John Bogle, was a student of history as much as he was a student of finance. To solve this behavioral trap, he often pointed to the ancient Greek myth of Odysseus and the Sirens.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the Odyssey, the hero Odysseus has to sail his ship past the island of the Sirens. He knows that their song is so beautiful, so seductive, that any man who hears it will be compelled to steer his ship into the rocks and drown. Odysseus is smart enough to know that he is not strong enough to resist. He does not rely on his willpower. He does not say, \"I will just listen a little bit and then turn away.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Instead, he creates a system to save him from himself. He orders his crew to plug their ears with beeswax so they cannot hear the noise. Then, he orders them to tie him tightly to the mast of the ship. He gives them strict instructions: \"No matter how much I scream, beg, or order you to untie me, you must bind me tighter.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Bogle argued that the Index Fund is the modern investor’s \"mast.\" By buying the entire market and committing to hold it \"forever,\" you are tying yourself to the mast of American capitalism. You are admitting that you cannot predict the squalls of the market or the seductive song of the next \"hot stock.\" The beeswax is your refusal to engage with daily market noise. The strategy is not to outsmart the Sirens; it is to remove your ability to grab the wheel when you are emotionally compromised. The investor who is \"tied to the mast\" of the index survives the crash; the one who thinks he can steer through the rocks always drowns.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Modern Context: The Notification Tsunami")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, the Sirens have upgraded their technology. They don't just sing from an island; they buzz in your pocket 30 times a day.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In Bogle’s era, if you wanted to panic-sell during a crash, you had to call a broker. That broker might be at lunch, or he might talk you out of it. There was friction. Today, the financial industry has removed every ounce of friction. You can FaceID and liquidate your life savings in three seconds while waiting for a latte.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("We are living through a \"Notification Tsunami.\" You wake up to a red alert: \"Crypto Crash: Bitcoin down 12% in 20 minutes.\" Then a push notification from a news app: \"Fed Chair Warns of 'Pain' Ahead.\" Then you open X (Twitter) and see a respected \"Finfluencer\" posting a chart that looks like a cliff, captioned \"It’s over.\" This constant barrage is weaponized psychology. It keeps your cortisol levels high and your finger hovering over the \"Sell\" button. The modern Siren song is the \"Gamification of Panic.\" Trading apps shower you with confetti when you buy and red flashing lights when you lose, treating your financial future like a dopamine-loop video game.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In an age of hyper-reactive AI, your edge isn't speed—it's \"stillness\". Algorithms are designed to fight over the noise of the millisecond; you win by ruthlessly committing to the silence of the decade!")
                    ),
                ],
                audioDurationSeconds: 316,
                currentProgress: 0.0
            ),
        ],
        7: [
            1: CoreChapterContent(
                chapterNumber: 1,
                chapterTitle: "The Valuation Duel",
                bookTitle: "A Random Walk Down Wall Street",
                bookAuthor: "Burton G. Malkiel",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Wall Street Civil War")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("There is a secret war happening on Wall Street, and you are the casualty. Most investors think the stock market is one big casino where everyone is playing the same game. You aren't. There are actually two rival tribes fighting for your money, and they speak completely different languages. If you don't know which tribe you belong to, you will buy for the wrong reason and sell at the wrong time. The friction is confusion: You buy a stock because \"it's going up\" (Tribe A) but panic when the earnings report is bad (Tribe B). You are mixing oil and water, and your portfolio is the one catching fire.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Castle vs. The Foundation")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author simplifies this chaos into two distinct philosophies.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("First, there is the \"Firm Foundation\" theory. Imagine a cynical old appraiser looking at a house. He doesn't care if the house is pretty. He cares about the rent it generates, the cash flow, the dividend. He believes every asset has an intrinsic value—an anchor. If the price drifts too far above the anchor, he sells. If it falls below, he buys. He is playing the long game of math.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Second, there is the \"Castle in the Air\" theory. This is the psychological game. The author describes this investor as someone building castles in the clouds. They don't care about the cash flow; they care about the crowd. They buy an asset simply because they believe a \"Greater Fool\" will come along tomorrow and pay more for it. They are surfing the waves of human hope and fear, not analyzing spreadsheets. The author warns that while \"Castles\" are more fun, they are built on nothing but air, and gravity eventually wins.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The 2026 Hybrid")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The lines have blurred, but the danger is higher. The \"Castle in the Air\" is now turbo-charged by social media. A meme coin or a hyped AI startup with zero revenue can skyrocket simply because a subreddit or a TikTok trend decided it should. This is pure \"Castle\" building. It works until the music stops.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Conversely, the \"Firm Foundation\" has become harder to find because algorithms have priced in \"intrinsic value\" instantly. You can't easily find an undervalued stock just by reading a balance sheet anymore—an AI bot beat you to it by 0.003 seconds. The modern \"Wiser\" investor knows that Bitcoin is a Castle (pure psychology, no cash flow) while a Dividend ETF is a Foundation. You can own both, but you must never confuse them. If you treat a Castle like a Foundation, you will lose everything waiting for a \"bounce back\" that never comes.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "Label Your Portfolio",
                                description: "Tonight, go through every single holding you own. Tag it \"Castle\" (speculative, relies on hype) or \"Foundation\" (has earnings/dividends).",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The 90/10 Rule",
                                description: "Ensure that \"Castle\" assets never exceed 10% of your total net worth. This is your \"fun money.\" The other 90% stays in the Foundation to pay for your actual life.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 201,
                currentProgress: 0.0
            ),
            2: CoreChapterContent(
                chapterNumber: 2,
                chapterTitle: "Spotting Financial Hallucinations",
                bookTitle: "A Random Walk Down Wall Street",
                bookAuthor: "Burton G. Malkiel",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The IQ Trap")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is the embarrassing truth: High IQ does not protect you from financial stupidity. In fact, smart people are often more susceptible to bubbles because they are better at rationalizing their madness. The friction isn't a lack of data; it is a surplus of dopamine. When you see your neighbor—who you know is less intelligent than you—making a fortune on a \"sure thing,\" your brain breaks. Envy overrides logic. You buy at the top not because you believe the math, but because you cannot bear the pain of missing out. You are not investing; you are hallucinating wealth.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The $50,000 Onion")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author inoculates you against this madness by taking you back to 17th-century Holland. He tells the story of Tulipmania, the original blueprint for every financial disaster since.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("It started innocently. Tulips were rare and beautiful. But soon, the \"Castle in the Air\" builders arrived. Prices detached from reality. At the peak, a single Semper Augustus bulb sold for the equivalent of a mansion, a carriage, and horses. The author recounts the tragic comedy of a sailor who mistook a rare bulb for an onion and ate it with his herring—a breakfast that cost a fortune. When the fever broke, the price collapsed by 99% overnight. The bulb was still a bulb; only the hallucination had vanished. The lesson? An asset's price is what you pay; its value is what it does. If it does nothing but sit there, it is a speculation, not an investment.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The 2026 \"New Paradigm\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Nowadays, we don't buy tulips; we buy \"narratives.\" The hallucination today wears a lab coat or a hoodie. It is the AI startup with no product but a $5 billion valuation. It is the \"Governance Token\" that governs nothing. It is the meme stock that rallies because an influencer posted a frog emoji.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("[Getty Images]")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The mechanism is identical to the 1600s: Innovation + Easy Money + Fear of Missing Out (FOMO). The modern trap is the phrase, \"This time is different.\" You will hear, \"But crypto is the future of money!\" or \"AI is the new electricity!\" The author would remind you: The internet was the future in 1999, but 95% of dot-com companies still went to zero. Being right about the technology does not mean you are right about the price.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Cocktail Party\" Indicator",
                                description: "The next time a taxi driver, a barber, or a random relative gives you a \"hot tip\" on a specific asset, take it as a sell signal. When the general public is \"all in,\" there is no one left to buy.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The \"Boring\" Test",
                                description: "If an investment makes your heart race, it’s gambling. Real investing should be as exciting as watching paint dry. If you are feeling adrenaline, close the app.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 188,
                currentProgress: 0.0
            ),
            3: CoreChapterContent(
                chapterNumber: 3,
                chapterTitle: "The Technical Analysis Trap",
                bookTitle: "A Random Walk Down Wall Street",
                bookAuthor: "Burton G. Malkiel",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Fortune Teller’s Guild")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Humans are desperate for patterns. If you stare at clouds long enough, you see faces. If you stare at stock charts long enough, you see money. This is the friction: You want to believe there is a secret code, a \"Head and Shoulders\" pattern or a \"Golden Cross\" that predicts the future. You think if you just study the lines on the screen hard enough, you can outsmart the market. You are wrong. The market isn't a map; it’s a mirror reflecting your own wishful thinking. The people selling you these \"systems\" aren't rich from trading; they are rich from selling you the system.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Coin Flip Experiment")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author destroys this entire industry—Technical Analysis—with a humiliating experiment. He asked his students to create a fake stock chart. They did this by flipping a coin: Heads meant the stock went up, Tails meant it went down. They plotted the results on graph paper.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The result? The chart looked exactly like a real stock. It had trends, cycles, and \"resistance levels.\" When the author showed this fake chart to a famous technical analyst, the expert got excited. He analyzed the \"formations\" and confidently shouted, \"Buy immediately! This stock is about to break out!\" The author then gently revealed the truth: \"This isn't a company. This is a coin flip.\" The lesson was brutal. Technical analysts are often just predicting the past. They are analyzing noise and calling it music. If past prices predicted future prices, librarians would be the richest people in the world.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Algorithmic Slaughterhouse")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The Technical Analysis trap is even deadlier because you aren't competing against a guy with a ruler; you are competing against High-Frequency Trading (HFT) bots. These AI algorithms analyze every tick, every pattern, and every millisecond of data. If there was a profitable pattern, the AI found it, exploited it, and erased it before your chart even loaded.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Modern \"technical traders\" on crypto exchanges or forex apps are essentially playing a video game against a supercomputer set to \"God Mode.\" You might see a \"support level\" at $50,000 for Bitcoin. The bot sees your \"Stop Loss\" order sitting right below it. The bot will push the price down just enough to trigger your sale, steal your shares, and then rally the price back up. You are not the trader; you are the liquidity.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Unsubscribe\" Purge",
                                description: "Go to your YouTube and social media. Unsubscribe from anyone drawing lines on a chart and promising \"breakouts.\" They are the modern-day palm readers.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The \"Chart-Blind\" Rule",
                                description: "Stop checking the price of your investments daily. The more often you look, the more likely you are to see a pattern that isn't there. Check once a quarter, not once an hour.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 186,
                currentProgress: 0.0
            ),
            4: CoreChapterContent(
                chapterNumber: 4,
                chapterTitle: "The Fundamental Illusion",
                bookTitle: "A Random Walk Down Wall Street",
                bookAuthor: "Burton G. Malkiel",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Trap of \"Doing Your Homework\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is the most painful lesson for smart people: You can do everything right and still lose. You can read every balance sheet, calculate every ratio, and perfectly model a company’s future cash flows. You can be the \"Warren Buffett\" of your friend group. But there is a fatal flaw in the logic of \"Fundamental Analysis.\" You believe that if you find a great company at a fair price, the stock must go up. The market does not care about your spreadsheet. The friction is that you are trying to predict the future with tools that only measure the past.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Blindfolded Monkeys")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author exposes this illusion with a famous challenge to the entire financial industry. He declared that a blindfolded monkey throwing darts at the financial pages of a newspaper could select a portfolio that would do just as well as one carefully selected by experts.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("This wasn’t a joke; it was a mathematical gauntlet. He explains that even the smartest fundamental analysts are often wrong because of \"Random Events.\" A company might have a perfect balance sheet today, but tomorrow a factory burns down, a CEO gets indicted, or a new law bans their product. These are \"unknown unknowns.\" The author tells the story of how analysts loved the \"Nifty Fifty\"—the blue-chip stocks of the 1970s that could \"never fail.\" Xerox, Polaroid, Avon. They had great fundamentals. But the market changed, technology shifted, and those \"safe\" stocks crashed by 80-90%. The lesson? Even valid information is useless if the market has already priced it in or if random chaos strikes tomorrow.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The AI Forecasting fallacy")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The illusion of \"predicting fundamentals\" has moved to AI. We now have \"Predictive Analytics\" and \"Big Data\" models that claim to foresee earnings surprises. But here is the catch: If the AI works, everyone uses it. If everyone uses it, the advantage disappears instantly.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The modern \"Fundamental Illusion\" is thinking you can out-analyze a market that is 90% machine-driven. You might think NVIDIA or the latest Quantum Computing stock is undervalued based on its P/E ratio. But the market isn't pricing it on P/E; it's pricing it on liquidity flows and macroeconomic sentiment. You are playing chess while the market is playing a slot machine. The fundamental data is real, but its predictive power is largely a mirage in the short term.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Humble Pie\" Audit",
                                description: "Look at your biggest losing stock. Did you buy it because you \"liked the company\" or \"used the product\"? Admit that your analysis was incomplete.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The \"5-Year\" Rule",
                                description: "If you buy a stock based on fundamentals, you must commit to holding it for 5 years minimum. Fundamental value eventually matters, but it takes years, not weeks, to play out. If you can't hold for 5 years, don't buy.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 190,
                currentProgress: 0.0
            ),
            5: CoreChapterContent(
                chapterNumber: 5,
                chapterTitle: "Respecting the Efficiency Engine",
                bookTitle: "A Random Walk Down Wall Street",
                bookAuthor: "Burton G. Malkiel",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Ego of the Picker")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is the hardest pill to swallow: You are not smarter than the market. Nobody is. The friction is your ego. You believe that because you read a few articles or follow a few experts, you have an \"edge.\" You think you can spot the next Apple or avoid the next crash before everyone else. This belief is expensive. Every time you try to outsmart the market by picking winners and losers, you are fighting a collective intelligence of millions of people, banks, and algorithms that have already priced in every piece of news you just read. You are betting against the wisdom of the crowd, and the house almost always wins.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The $100 Bill on the Sidewalk")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author illustrates this with a classic economist joke. Two economists are walking down the street. One spots a $100 bill lying on the ground. \"Look!\" he says. \"A hundred dollars!\" The other economist doesn't even stop walking. \"It can't be real,\" he replies. \"If it were a real $100 bill, someone would have already picked it up.\"")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("[image]")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("This is the essence of the Efficient Market Hypothesis (EMH). The author explains that news travels so fast—instantly—that prices adjust immediately. By the time you hear good news about a company, the stock price has already gone up to reflect it. There are no \"free lunches\" (or $100 bills) lying around waiting for you to find them. The market is an \"Efficiency Engine\" that devours information and spits out the correct price before you can even click \"Buy.\"")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Speed of Light")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Now, the Efficiency Engine has gone nuclear. Information doesn't just travel fast; it travels at the speed of light via fiber optic cables directly into the servers of High-Frequency Trading firms. If an AI detects a positive sentiment in a CEO's voice during an earnings call, it buys the stock before the CEO finishes the sentence.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The modern investor who tries to \"beat the market\" is bringing a knife to a laser fight. You might think you found an \"undiscovered\" gem in the crypto market or a small-cap biotech stock. You didn't. You are just the last person to know. The \"Wiser\" move is to stop trying to beat the engine and instead hitch a ride on it. If you can't find the needle in the haystack, buy the whole haystack.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"News Fade\" Rule",
                                description: "When you see a headline like \"Stock X Soars on Earnings Beat,\" do not buy. The move is over. You are buying the exhaust fumes, not the rocket.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The Index Commitment",
                                description: "Accept that \"Average\" is actually \"Elite.\" By buying a total market index fund, you guarantee you will outperform the vast majority of professional pickers who waste money trying to beat the efficiency.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 188,
                currentProgress: 0.0
            ),
            6: CoreChapterContent(
                chapterNumber: 6,
                chapterTitle: "The Art of Risk Engineering",
                bookTitle: "A Random Walk Down Wall Street",
                bookAuthor: "Burton G. Malkiel",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"Fake Diversity\" Scam")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Most investors are walking around with a ticking time bomb in their pockets, and they call it a \"diversified portfolio.\" You think because you own Apple, Microsoft, NVIDIA, and Google, you are safe. You aren't. You just own four different flavors of the exact same risk. If the tech sector catches a cold, your entire financial life gets pneumonia. The friction here is the misunderstanding of correlation. You are not engineering risk; you are piling it up in one corner and hoping gravity doesn't notice.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Island of Sun and Rain")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author explains the cure for this madness—Modern Portfolio Theory—with a simple parable about an island economy. Imagine there are only two companies on this island: a large beach resort and an umbrella manufacturer.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("If you invest all your money in the resort, you make a fortune when it's sunny, but you go broke when it rains. It’s a rollercoaster. If you invest only in the umbrella company, you feast during storms but starve during the summer.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The \"Free Lunch\" of economics appears when you buy both. Because their fortunes move in opposite directions (negative correlation), the volatility cancels out. You earn a steady, reliable return regardless of the weather. The author demonstrates that by combining volatile assets that zig when others zag, you can actually reduce your total risk without lowering your expected return. This is the mathematical miracle of diversification.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The 2026 Correlation Crisis")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Now, the \"Island\" is global, and finding assets that don't move together is harder than ever. During a panic, \"all correlations go to one.\" When the market crashes, almost everything—stocks, real estate, even crypto—dumps together.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The mistake today is thinking that Bitcoin is a \"hedge\" against the stock market. It often isn't; it’s just a high-beta tech stock that trades 24/7. True \"Risk Engineering\" in the modern era requires uncomfortable assets. It means holding boring government bonds, perhaps managed futures, or commodities like gold that actually move differently than your AI stocks. If your entire portfolio makes you happy at the same time, you are not diversified. You should always hate at least one thing you own.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Stress Test\"",
                                description: "Look at your portfolio during the last market dip (e.g., last month). Did everything go down? If yes, you are not diversified.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The Un-Correlated Hunt",
                                description: "Find one asset class that has a low or negative correlation to your main holdings (often Bonds or Commodities) and allocate 5-10%. It’s insurance, not a lottery ticket.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 170,
                currentProgress: 0.0
            ),
            7: CoreChapterContent(
                chapterNumber: 7,
                chapterTitle: "Decoding \"Smart\" Strategies",
                bookTitle: "A Random Walk Down Wall Street",
                bookAuthor: "Burton G. Malkiel",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"New Coke\" of Finance")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Wall Street has a massive problem: Simple index funds work too well. They are cheap, effective, and boring. This is a disaster for fund managers who need to buy yachts. Their solution? Re-branding. They realized they couldn't sell \"Active Management\" anymore because the data proved it failed. So, they invented a sexy new term: \"Smart Beta.\" The friction here is marketing. You are being sold a product that claims to be \"smarter\" than the market—offering higher returns with lower risk. It sounds perfect. It is almost always a trap designed to extract higher fees for a slightly tweaked product.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Tilt and the Trap")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author dissects this trend with the precision of a surgeon. He explains that \"Smart Beta\" strategies essentially take a boring index (like the S&P 500) and \"tilt\" it. Instead of buying companies based on size (Market Cap), they buy based on \"Factors\" like Value (cheap stocks), Momentum (rising stocks), or Low Volatility (stable stocks).")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He admits that historically, some of these factors have outperformed. Small companies and cheap \"value\" stocks had a good run in the 20th century. However, he warns that these anomalies often disappear the moment they are discovered. It’s like a secret fishing spot. Once you publish the coordinates in a magazine (or launch an ETF), the fish vanish. The \"Smart\" strategy often ends up being just a \"More Expensive\" strategy that underperforms the dumb index once you subtract the taxes and trading costs.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The 2026 \"Factor Zoo\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The \"Smart\" marketing has evolved into \"Thematic\" and \"AI-Driven\" madness. We now have the \"Factor Zoo\"—hundreds of complex ETFs claiming to exploit obscure market inefficiencies. You might see an \"AI-Powered Sentiment ETF\" or a \"Work-From-Home Alpha Fund.\" These are the grandchildren of Smart Beta.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The danger today is \"Overfitting.\" With supercomputers, I can find a correlation between anything. I can prove that buying stocks with the letter \"Q\" in their name outperforms the market on Tuesdays. An AI can generate a backtest that looks perfect. But it isn't a strategy; it's a coincidence. The modern \"Smart\" fund is often just a closet index fund charging you 0.75% for a service a robot does for 0.03%.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"0.20% Line in the Sand\"",
                                description: "Check the Expense Ratio of every \"Smart\" or \"Factor\" ETF you own. If you are paying more than 0.20% per year, you are likely being harvested. Sell it and buy the \"dumb\" version.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The \"Complexity Penalty\"",
                                description: "If you cannot explain why a fund outperforms in one sentence (e.g., \"It buys cheap companies\"), do not buy it. If the strategy involves a \"proprietary black box algorithm,\" run.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 178,
                currentProgress: 0.0
            ),
            8: CoreChapterContent(
                chapterNumber: 8,
                chapterTitle: "Conquering Your Inner Ape",
                bookTitle: "A Random Walk Down Wall Street",
                bookAuthor: "Burton G. Malkiel",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Caveman in the Casino")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is the biological glitch that destroys portfolios: Your brain is 200,000 years old. It was designed to survive on the savannah, not to trade derivatives on a smartphone. When you see a stock crash, your amygdala—the fear center—lights up exactly as if a tiger were chasing you. You panic and sell. When you see a stock soaring, your dopamine centers light up like you just found a fruit tree. You buy. The friction is that successful investing requires you to do the exact opposite of your survival instincts. You are biologically hardwired to buy high (greed) and sell low (fear). You are not a rational economic actor; you are an emotional ape with a bank account.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Pain of the Loss")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author drags this psychological flaw into the light using Behavioral Finance. He explains a concept called Loss Aversion with a brutal truth: The pain of losing $1,000 is twice as intense as the pleasure of gaining $1,000.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("This imbalance causes a deadly mistake called the \"Disposition Effect.\" Investors tend to sell their winning stocks too early (to \"lock in\" the good feeling) and hold their losing stocks too long (to avoid the pain of admitting a mistake). He tells the story of the investor who refuses to sell a tanking stock, saying, \"I'll sell it when it gets back to even.\" It rarely does. By refusing to \"realize\" the loss, the investor turns a small scratch into a fatal wound, anchoring their ship to a sinking rock just to protect their ego.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Dopamine dispenser")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The financial industry has weaponized your biology against you. Trading apps are no longer tools; they are video games. When you buy a stock, confetti explodes on the screen. When you check your crypto portfolio, the numbers flash in bright green or red, specifically designed to trigger a dopamine loop.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The modern \"Inner Ape\" falls for Confirmation Bias on steroids. If you buy a meme coin that crashes, you don't sell. Instead, you go to a Discord server or a Reddit thread full of other bag-holders who tell you to have \"Diamond Hands.\" They reinforce your delusion that the price must come back up. In the 1970s, you had to call a broker to make a mistake. Today, you can ruin your financial future with a single face-ID unlock while sitting on the toilet.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"24-Hour\" Cooling Rule",
                                description: "If you want to buy or sell a specific stock (not an index fund), write it down. You are not allowed to execute the trade until 24 hours have passed. If the urge is emotional, it will fade. If it is rational, it will stay.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The \"Kill the Loser\" Audit",
                                description: "Look at your portfolio. Identify the stock with the biggest loss. Ask yourself: \"If I didn't own this today, would I buy it at this price?\" If the answer is \"No,\" sell it immediately. Take the tax loss and move on.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 200,
                currentProgress: 0.0
            ),
            9: CoreChapterContent(
                chapterNumber: 9,
                chapterTitle: "The Indexing Manifesto",
                bookTitle: "A Random Walk Down Wall Street",
                bookAuthor: "Burton G. Malkiel",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Parasite in Your Portfolio")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is the scandal of the century: The financial industry is the only industry on Earth where you get worse service the more you pay for it. If you hire a cheap lawyer, you go to jail. If you hire a cheap heart surgeon, you die. But if you hire an expensive fund manager, you go broke. The friction is that we are trained to believe \"You get what you pay for.\" In investing, you get what you don't pay for. Every dollar you pay in management fees, trading costs, and taxes is a dollar that isn't compounding for your future. You are feeding a system of parasites that produce nothing but excuses for why they failed to beat the average.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Ultimate \"Cheat Code\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author presents the \"Index Fund\" not as a compromise, but as a mathematical weapon. He explains that the stock market is a \"positive-sum game\"—corporate profits grow, and everyone can win. But active trading is a \"zero-sum game\"—for every person who beats the market, someone else must lose.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Once you add the fees of the \"croupiers\" (brokers and managers), it becomes a \"negative-sum game.\" The author champions the solution: Buy the haystack. Don't look for the needle. By buying a broad-based Index Fund (like the S&P 500), you own a tiny slice of every major business in America. You fire the expensive manager. You eliminate the risk of picking a loser. You guarantee that you will capture the entire return of the market, minus a tiny fee. He proves that over a 20-year period, this \"dumb\" strategy beats 90% of the \"smart\" money. It is the only free lunch on Wall Street.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Self-Healing Beast")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In 2026, the Index Fund is even more powerful because of velocity. The modern S&P 500 is a ruthless, self-cleaning organism. When a company (like an old retail giant) starts to die, the Index automatically demotes it. When a new innovator (like an AI giant) rises, the Index automatically adds it.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("You don't need to predict if \"Quantum Computing\" will replace \"Generative AI.\" The Index will do it for you. If a new technology takes over the world, it will enter the Index, and you will own it automatically. The modern danger is \"Closet Indexing\"—paying high fees for a \"Tech ETF\" that just owns the same top 5 stocks as the S&P 500 anyway. The \"Wiser\" move is to accept that the Index is the ultimate trend-follower. It never sleeps, it never panics, and it costs almost nothing.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"0.10%\" Ultimatum",
                                description: "Check the Expense Ratio of your main funds. If you are paying more than 0.10% (10 basis points) for a U.S. stock fund, you are being ripped off. Move to a low-cost provider immediately.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The \"Set and Forget\" Order",
                                description: "Automate a monthly transfer into a Total Market Index Fund. Do not look at the price. Do not read the news. Just buy. The only way to lose with this strategy is to stop.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 203,
                currentProgress: 0.0
            ),
            10: CoreChapterContent(
                chapterNumber: 10,
                chapterTitle: "The Lifecycle Wealth Map",
                bookTitle: "A Random Walk Down Wall Street",
                bookAuthor: "Burton G. Malkiel",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Sleeping Bag Strategy")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is the most expensive mistake smart people make: They treat investing like a static number. They ask, \"What is the best portfolio?\" as if the answer is the same for a 22-year-old software engineer and a 65-year-old retiree. It isn't. The friction is Risk Capacity. A young person can lose 50% of their money and laugh about it because they have 40 years of \"Human Capital\" (future earnings) left to replace it. A retiree who loses 50% faces a catastrophic decline in lifestyle. Most people fail because they are either too conservative when young (sleeping on a pile of cash that inflation is eating) or too aggressive when old (sleeping on a bed of crypto that might vanish overnight).")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Glide Path")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author solves this by introducing the \"Lifecycle Guide\"—a roadmap that shifts as you age. He doesn't just give you a pie chart; he gives you a movie script.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In Act 1 (your 20s and 30s), you are aggressive. You own almost entirely stocks. You want volatility because you are a net buyer—when the market crashes, stocks are on sale for your future self. In Act 2 (your 40s and 50s), the plot thickens. You start adding bonds and real estate. You are no longer just growing wealth; you are protecting it. In Act 3 (your 60s and beyond), the climax arrives. You shift heavily into income-producing assets like bonds and dividend stocks. The goal is no longer \"more money\"; the goal is \"reliable money.\" The author warns that failing to follow this \"Glide Path\" is like trying to land a plane without lowering the landing gear—you might crash right at the end of a perfect flight.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The 2026 Longevity Gamble")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In 2026, the old \"100 minus your age\" rule for stock allocation is dangerous not because you will live to 100, but because you might. The greatest risk to a modern retirement isn't the market crashing; it is Longevity Risk—the mathematical probability that you survive longer than your savings do.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Medical advances and lifestyle shifts mean your retirement could easily stretch for 30 or 40 years. If you shift entirely to \"safe\" bonds at age 65, inflation becomes your silent assassin. A 3% inflation rate cuts your purchasing power in half every 24 years. If you live to 90 but planned for 80, you spend your final decade broke.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The modern \"Wiser\" strategy requires you to stay invested in growth assets (stocks) much later in life than your grandparents did. You need your portfolio to keep working because you might be retired for a third of your life. The goal isn't just to reach the finish line; it's to ensure the gas tank doesn't run dry while you are still driving.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Target Date\" Hack",
                                description: "If you hate math, buy a \"Target Date Fund\" for the year you turn 65 (e.g., \"Target Retirement 2060\"). It automatically adjusts the \"Glide Path\" for you. It is the autopilot for wealth.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The \"Human Capital\" Audit",
                                description: "If you are young and your job is unstable (e.g., a startup), your portfolio should be slightly safer. If your job is tenured (e.g., government), your portfolio can be more aggressive. Your job is a \"bond\"; treat it like one.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 220,
                currentProgress: 0.0
            ),
        ],
        8: [
            1: CoreChapterContent(
                chapterNumber: 1,
                chapterTitle: "The Ticker Tape Illusion",
                bookTitle: "The Essays of Warren Buffett",
                bookAuthor: "Warren Buffett",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is the friction: most investors treat the stock market like a casino and their shares like betting chips. They obsess over the flashing numbers (the price), completely detached from the reality of what those numbers represent. When the \"chip\" value drops, they panic and fold. This is the \"Renter’s Mentality\"—you are just renting a position on a chart, hoping to pass it to a greater fool for a profit. This detachment is the single greatest cause of wealth destruction. You cannot weather a storm if you don't even know what house you are living in.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The \"Silent Partner\" Letter Buffett flips this dynamic by ignoring the stock market entirely. In his foundational Owner-Related Business Principles, he tells a simple but radical story: imagine Berkshire Hathaway isn't a publicly traded corporation, but a private family partnership.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He posits that he is the managing partner, and you are the silent partner. You aren't a \"shareholder\"—a faceless source of capital to be exploited—you are a co-owner. Buffett famously notes that he treats his investors as if they were his own sisters and aunts. An owner of a private farm doesn't check the price of their land every day; they check the weather and the crop yield. Buffett teaches that if you wouldn't be comfortable owning a stock if the market closed down for five years, you shouldn't own it for five minutes.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The Gamification Trap In the modern era, \"Adopting the Owner's Mindset\" is harder—and more profitable—than ever. Financial apps have weaponized the \"Renter's Mentality\" through gamification. When you buy a stock on a modern app and see confetti explode, or panic-sell because a crypto coin dropped 15% in an hour, you are falling into the trap Buffett warned against.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Apply the \"Owner's Mindset\" to the AI boom. The \"Renter\" buys NVIDIA because the line is going up. The \"Owner\" buys it because they understand the moat of the CUDA software ecosystem and want to own a proportional share of that cash flow for the next decade. If you are stressing over quarterly earnings calls, you are likely speculating, not owning.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Napkin Pitch\" Protocol",
                                description: "Pick one company you are interested in. On a single napkin (or sticky note), draw a simple diagram of exactly how cash enters the company and how it leaves (expenses).",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 157,
                currentProgress: 0.0
            ),
            2: CoreChapterContent(
                chapterNumber: 2,
                chapterTitle: "Decoding the True Economics",
                bookTitle: "The Essays of Warren Buffett",
                bookAuthor: "Warren Buffett",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The Profit Mirage Here is the dangerous secret about financial statements: \"Profit\" is an opinion, but \"Cash\" is a fact. Most aspiring investors glance at the bottom line of an Income Statement—Net Income—and assume that’s how much money the business actually made. They are wrong.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("GAAP (Generally Accepted Accounting Principles) is a set of rules designed to make companies comparable, not necessarily truthful about their economic reality. You can follow every accounting rule perfectly and still go bankrupt while reporting a \"profit.\" If you are analyzing a business based solely on its Price-to-Earnings (P/E) ratio, you are effectively driving a car by looking only at the speedometer while ignoring the fuel gauge. You know how fast you're going, but not when the engine is about to die.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"Owner Earnings\" Revelation")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Buffett solved this by inventing his own metric: \"Owner Earnings.\" In his letters, he describes a hypothetical business that reports massive profits but requires new machinery every year just to stay open. To the accountant, buying that machine is just capital expenditure (CapEx) that gets depreciated slowly over years. To the owner, that money is gone. It left the bank account. It cannot be used to pay dividends or expand.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Buffett’s formula is ruthless: Take reported earnings, add back depreciation (a non-cash charge), but then subtract the capital expenditures required to maintain the company's competitive position. This reveals the \"distributable cash\"—the actual money the owner can take out of the business without hurting it. He teaches us to look for companies that don't just report accounting profits, but generate \"free cash flow\"—businesses that don't require heavy reinvestment just to survive.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The \"Adjusted EBITDA\" Trap In today’s tech-dominated market, this distortion is even worse. We live in the era of \"Adjusted EBITDA\"—a metric Charlie Munger famously called \"bullshit earnings.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Modern tech companies (SaaS, AI, Gig Economy) often report massive losses but claim they are profitable on an \"Adjusted\" basis. They do this by excluding things like Stock-Based Compensation (paying employees in stock instead of cash). They pretend this isn't a real expense. But if you are an owner, it is a real expense—it dilutes your ownership stake.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Consider the recent AI boom. A company might show high EBITDA, but if they have to spend billions every year on new GPU clusters just to keep their model relevant, their Owner Earnings might be negative. They are running on a treadmill, running faster and faster just to stay in the same place.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The Capex Check: Open the Cash Flow Statement and compare \"Capital Expenditures\" (under Investing) to \"Depreciation\" (under Operating). If Capex is consistently higher than Depreciation, the company is burning cash just to stay alive rather than generating wealth for you—proceed with extreme caution.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The \"Real\" P/E: Ignore the standard P/E ratio found on finance sites. Calculate \"Price to Free Cash Flow\" by dividing Market Cap by (Operating Cash Flow minus Capital Expenditures) to see the true multiple you are paying for the cash the business actually generates.")
                    ),
                ],
                audioDurationSeconds: 201,
                currentProgress: 0.0
            ),
            3: CoreChapterContent(
                chapterNumber: 3,
                chapterTitle: "Determining the Strength of the Moat",
                bookTitle: "The Essays of Warren Buffett",
                bookAuthor: "Warren Buffett",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The \"Good Product\" Fallacy Here is the friction: most investors think a \"good company\" is one with a great product or a fast-growing market. They see a revolutionary technology or a popular brand and assume the stock is a winner. This is the \"Good Product Fallacy.\" History is littered with companies that changed the world but went bankrupt because they couldn't protect their profits. If you are buying a stock just because \"everyone is using it,\" you are missing the most critical variable: durability. A castle with no moat is just a pile of gold waiting to be looted by competitors.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Castle and the Knight")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Buffett solves this with his most famous metaphor: the \"Economic Moat.\" He asks you to imagine a business as a magnificent castle. Inside the castle is the \"Gold\"—the high returns on invested capital. But every day, a thousand knights (competitors) ride up to the castle walls, trying to steal that gold.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He distinguishes between two types of businesses: \"Franchises\" and \"Commodities.\" A Franchise is a castle with a wide, crocodile-filled moat. It has pricing power—it can raise prices, and customers still pay. Think of See's Candies, a company Buffett bought. They could raise the price of a box of chocolates every single year, and people would still buy it for Valentine's Day because no one wants to give their spouse \"discount\" chocolate. A Commodity business, on the other hand, has no moat. It must compete solely on price. If it raises prices by a penny, customers leave. Buffett teaches that it is far better to buy a wonderful business (Franchise) at a fair price than a fair business (Commodity) at a wonderful price.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The \"Network Effect\" Moat In the modern economy, moats have evolved from physical brands to digital networks. The most powerful moat today is the \"Network Effect\"—where a product becomes more valuable as more people use it.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Consider the \"AI War.\" A standalone AI wrapper app has a weak moat; anyone can copy the code and undercut the price. But a platform like the Apple App Store or Microsoft's enterprise ecosystem has a massive moat. If you are a developer, you must be on the App Store because that's where the users are. If you are a user, you stay because that's where the apps are. This creates a \"Switching Cost\" so high that users are effectively locked in. In the age of remote work and digital subscriptions, looking for high \"Switching Costs\" is the modern equivalent of looking for a brand name. If it takes a company 12 months and $10 million to migrate away from a software provider, that provider has a moat of steel.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Price Hike\" Test",
                                description: "Mentally simulate a 10% price increase for the company’s core product tomorrow. If the immediate customer reaction would be to switch to a competitor, the company has no moat (it's a Commodity). If they would grumble but pay anyway, it has a moat (it's a Franchise).",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The \"Switching Cost\" Audit",
                                description: "Research the company's customer retention rate or \"churn.\" If the company sells to businesses, look for how embedded their product is in the client's daily workflow. High integration equals high switching costs, which is a powerful, invisible moat.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 219,
                currentProgress: 0.0
            ),
            4: CoreChapterContent(
                chapterNumber: 4,
                chapterTitle: "Mastering Market Psychology",
                bookTitle: "The Essays of Warren Buffett",
                bookAuthor: "Warren Buffett",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Manic Neighbor")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("You believe the market is an authority figure. When a stock price collapses by 10% in a single morning, your primal brain assumes the market \"knows\" something you don’t. You feel foolish, exposed, and desperate to stop the pain. You assume price equals truth. But in the short run, the market is not a weighing machine that measures value; it is a voting machine that measures popularity. If you let the daily ticker dictate your emotional state, you are letting a mob of strangers determine your self-worth.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Legend of Mr. Market")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Buffett didn’t invent the solution to this; he inherited it from his mentor, Ben Graham, in the form of a parable that changes everything.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Imagine you own a small business in partnership with a man named \"Mr. Market.\" Every single morning, without fail, Mr. Market knocks on your office door. He is a manic-depressive. Some days, he is euphoric, sees only sunshine, and offers to buy your share of the business for a ludicrously high price. Other days, he is despondent, sees only ruin, and offers to sell you his share for pennies on the dollar.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author teaches us that Mr. Market is there to serve you, not to guide you. His wallet is open, but his wisdom is non-existent. You have three choices: you can ignore him, you can sell to him when he is manic, or you can buy from him when he is depressed. The one thing you must never do is fall under his influence. If you look at a dropping price and think, \"I must be wrong,\" you have allowed the crazy neighbor to run your house.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The Algorithm Is the New Mr. Market In the 1950s, Mr. Market showed up once a day in the morning newspaper. Today, he lives in your pocket, screaming at you 24/7 through push notifications and red/green candlesticks.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Modern apps have gamified this manic behavior. They use bright red colors and downward arrows to trigger a cortisol spike (fear) and bright green confetti to trigger dopamine (greed). In the world of Crypto and 24-hour trading, Mr. Market never sleeps; he is on a permanent bender. If you check your portfolio five times a day, you are inviting a manic-depressive into your brain five times a day to scream at you. The \"wiser\" investor realizes that volatility is not risk—it is the fee you pay for entrance. If you can decouple your mood from the color of the pixels on your screen, you have an insurmountable advantage over the algorithm-addicted masses.")
                    ),
                ],
                audioDurationSeconds: 173,
                currentProgress: 0.0
            ),
            5: CoreChapterContent(
                chapterNumber: 5,
                chapterTitle: "The Art of Capital Deployment",
                bookTitle: "The Essays of Warren Buffett",
                bookAuthor: "Warren Buffett",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The CEO’s Secret Addiction Here is the friction: You assume the CEO works for you. You assume their primary goal is to make your shares more valuable. In reality, most CEOs suffer from a \"growth addiction.\" Their salaries, prestige, and magazine covers are often tied to the size of the company (total revenue), not the value per share.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("This creates a conflict of interest. A CEO will often take the cash the business generates—your cash—and light it on fire by buying a mediocre competitor at a premium price, just to make the empire bigger. They call it \"strategic synergy.\" Usually, it’s just ego. If you don't watch how management spends the money in the bank, you are letting a teenager with a credit card run your household.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"One Dollar\" Ultimatum")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Buffett counters this with a ruthless rule known as the \"One Dollar Test.\" In his letters, he lays out a simple ultimatum for management: A company should only retain earnings if, for every dollar retained, it creates at least one dollar of market value over time.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He tells the story of how easy it is for a CEO to \"buy\" growth. Anyone can grow sales by buying a bad business. But a true \"Capital Allocator\" (Buffett's highest praise) knows when to shrink. If the company cannot reinvest cash at a high rate of return, the CEO must have the humility to give it back to the shareholders—either through dividends (a direct check) or share buybacks (increasing your percentage of ownership). He views a share buyback not as a market signal, but as a way to buy out \"unhappy partners\" at a discount, instantly making the remaining partners richer without them lifting a finger.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The Buyback Masquerade In the modern tech era, the \"Share Buyback\" has been corrupted. Many Silicon Valley giants announce massive, multi-billion dollar buyback programs to great fanfare. It sounds like they are returning capital to you. Often, they are lying.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("They are using those buybacks to offset \"Stock-Based Compensation\" (SBC). They pay their engineers in millions of new shares (diluting you), then use company cash to buy those shares back (undiluting you). The net result? The share count stays flat, but the cash is gone. This is running in place. A \"wiser\" analysis looks at the net change in share count. If a company spends $10 billion on buybacks but the share count doesn't drop, that wasn't a return of capital—it was a disguised employee salary expense.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The Buyback Truth Test",
                                description: "Don't trust the headline \"Company X authorizes $10 Billion Buyback.\" Verify it. Open the company's financial statements and look at the \"Weighted Average Shares Outstanding\" line on the Income Statement over the last 3-5 years. If this number hasn't decreased significantly—despite the company spending billions on \"Repurchase of Common Stock\" (found in the Cash Flow Statement)—they are simply using your shareholder cash to offset the dilution from their own employee stock options. A true buyback reduces the share count, increasing your percentage of ownership; anything else is a disguised expense.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 206,
                currentProgress: 0.0
            ),
            6: CoreChapterContent(
                chapterNumber: 6,
                chapterTitle: "Escaping the Institutional Trap",
                bookTitle: "The Essays of Warren Buffett",
                bookAuthor: "Warren Buffett",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The Boardroom Blind Spot We treat CEOs like chess grandmasters. We assume that when a leader announces a massive acquisition or a pivot into a new technology, they have run thousands of simulations and found the optimal move. But inside the boardroom, a different, darker game is being played. It isn’t strategy; it’s survival.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Most corporate leadership is not driven by logic, but by a terrifying fear of being the \"odd one out.\" If Competitor A buys a blockchain startup, Competitor B feels a primal compulsion to buy one too, regardless of the price. If they don't, and blockchain succeeds, they look like fools. If they buy it and it fails, well, \"everyone else did it too.\" This herd mentality is the single greatest destroyer of shareholder capital, yet it parades itself as \"industry standard best practice.\"")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"Institutional Imperative\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Buffett coined a specific term for this force: the \"Institutional Imperative.\" In his letters, he dissects how decent, intelligent managers turn into irrational copycats the moment they enter a corporate hierarchy.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He compares it to a teenager smoking to look cool. The Imperative dictates that if an investment banker proposes a deal, the CEO will find a way to justify it. If a competitor expands, the CEO will expand. Buffett famously noted that this force is so strong it can make a group of brilliant people do things that any one of them, acting alone, would call insane. He teaches us that the rarest asset in business is not intelligence—it is the courage to say \"no\" when everyone else is shouting \"yes.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The \"Pivot\" Pandemic In the modern era, the Institutional Imperative has accelerated into a virus. We see it in the \"Pivot\" culture.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("First, it was the \"Dot-com\" pivot. Then, the \"Blockchain\" pivot. Now, it is the \"AI\" pivot. Companies that sell pet food or furniture have no business declaring themselves \"AI-first\" organizations, yet they launch half-baked chatbots just to boost their stock price for a quarter.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Consider the Metaverse hype cycle of 2021. Facebook changed its name to Meta, and suddenly, every Fortune 500 company felt the Imperative to buy virtual real estate. Billions of dollars of shareholder capital were incinerated in months because CEOs were terrified of missing out on a future they didn't even understand. A \"wiser\" investor spots these waves of hysteria. When a CEO uses the latest buzzword 50 times on an earnings call but cannot explain the unit economics, they are not innovating; they are succumbing to the Imperative.")
                    ),
                ],
                audioDurationSeconds: 169,
                currentProgress: 0.0
            ),
        ],
        9: [
            1: CoreChapterContent(
                chapterNumber: 1,
                chapterTitle: "The Grand Illusion of Wall Street",
                bookTitle: "The Little Book that Still Beats the Market",
                bookAuthor: "Joel Greenblatt",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Listen closely. We are navigating an era of unprecedented financial chaos. Between inflation silently eating away your purchasing power and the manic volatility ripping through the markets, the average retail investor is wildly under-equipped. They are stepping onto a battlefield armed with a toy hammer. The prevailing myth—the grand illusion keeping the middle class stuck in mediocrity—is that the traditional financial industry exists to help you build wealth.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("It doesn't. The system is a brilliantly engineered machine designed to siphon your capital. When you walk into a traditional brokerage, you aren't meeting a wealth-building partner. Your stockbroker is typically paid a fee simply to sell you an investment product. They do not get paid to actually make you money. And if you think you can hide in a standard mutual fund, the math is brutally unforgiving. After the management companies extract their exorbitant fees and expenses, the vast majority of mutual funds consistently fail to beat the market averages over time. You are paying premium prices for guaranteed mediocrity.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Truth About the Tooth Fairy")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Joel Greenblatt strips away this glossy facade with one blunt, cynical truth: when it comes to Wall Street, there ain’t no tooth fairy. Once you leave the comfort of your home and step into the market, no one is going to tuck you in, no one is looking out for your best interests, and money is certainly not going to magically appear under your pillow. You are entirely on your own.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("To survive this, you need an ironclad anchor. Greenblatt illustrates the ease of destroying capital by recounting a childhood mistake: taking his hard-earned savings and buying a 10-foot-tall weather balloon from a mail-order catalog. After managing to inflate it, the balloon immediately floated away and popped on a tree down the street. It is a perfect metaphor for what happens when you throw capital at flashy, useless assets instead of saving for the future.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("To prevent your wealth from floating away, you must establish an absolute, non-negotiable baseline. That baseline is the 10-year U.S. government bond. If you lend your money to the U.S. government, they guarantee your interest rate and the return of your capital with essentially no risk. If that risk-free rate is 6%, then any other individual or business asking for your money must expect to pay you significantly more than 6%. If an investment cannot violently clear that risk-free hurdle, taking on the added risk is pointless; you are practically throwing money away.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The AI Gold Rush and Dusty Garages")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Fast forward to today's market, and we are watching the exact same wealth-destruction play out, just with sleeker marketing. Instead of mail-order weather balloons, retail investors are being lured by TikTok gurus hawking the latest dog-themed meme coins, or they are sinking capital into unproven AI startups that are nothing more than dusty garages with a flashy \".ai\" domain name. The noise surrounding electric vehicles, remote-work infrastructure, and massive data center build-outs is deafening.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But the math of the universe hasn't changed one bit. When you look at an overhyped tech ETF or a highly-leveraged crypto protocol, you have to ruthlessly apply the baseline. Does this asset definitively offer a return that crushes the risk-free rate of a government bond? If you are paying 2% management fees to a hedge fund that underperforms a basic Treasury bill, you are being robbed in broad daylight. The modern financial machine thrives on this complexity to hide the fact that they are feeding off your capital. The only way to find the gold beneath the gloss is to reject the noise entirely and demand a massive premium for your risk.")
                    ),
                ],
                audioDurationSeconds: 246,
                currentProgress: 0.0
            ),
            2: CoreChapterContent(
                chapterNumber: 2,
                chapterTitle: "The Myth of the Rational Machine",
                bookTitle: "The Little Book that Still Beats the Market",
                bookAuthor: "Joel Greenblatt",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the realms of computer science and finance, we are trained to search for elegant algorithms and rational models to predict behavior. We desperately want to believe the financial system is a perfectly calibrated machine. It is a fatal miscalculation. The average retail investor stares at a screen, watches a ticker flash green and red, and assumes those numbers represent the absolute, fundamental truth of a company's worth in real-time. This is exactly why the herd gets slaughtered. When inflation surges or volatility rips through the system, they scramble, treating the stock market like a hyper-efficient calculator. They are chasing the manic highs and panicking during the chaotic lows, convinced the market knows something they don't. It doesn't.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("Meet Your Manic Partner")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Joel Greenblatt shatters this illusion using a brilliant concept originally conceived by Benjamin Graham: imagine you are a partner in a business with a deeply unstable, wildly emotional guy named Mr. Market. Every single day, Mr. Market knocks on your door and offers to acquire your half of the business, or part with his half, at a specific price. Some days, he is absolutely euphoric, convinced the business is invincible, and he names an absurdly high price. Other days, he is profoundly depressed, terrified of the future, and offers to part with his stake for pennies on the dollar.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Greenblatt notes that in a single year, a massive company like General Motors might be valued at $30 billion one month, and $60 billion just a few months later. Did the intrinsic value of GM’s factories, inventory, and global operations magically double in 90 days? Absolutely not. The business didn't change; Mr. Market's mood changed. Your entire edge comes from exploiting these depressive panics to acquire pieces of a business with a massive \"margin of safety\"—securing your stake at a steep discount to true value. And while Mr. Market is an emotional basket case in the short term, over the long haul, facts and reality take over. Greenblatt promises that if you wait him out—usually within two to three years—Mr. Market will eventually sober up and pay a fair price for that value.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Modern Context: Bidding on Dusty Garages in the Digital Age")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Let's look at the absolute circus happening today. The financial media and online forums are relentlessly hyping the inevitable future of EVTOL flying cars and speculative crypto protocols. Mr. Market hears this noise, gets swept up in the mania, and suddenly prices a fundamentally unprofitable electric vehicle startup—essentially a dusty garage with a sleek 3D rendering—as if it has already conquered the global auto industry.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Conversely, when a minor supply chain glitch hits or trade war headlines spook the algorithmic trading bots, Mr. Market panics. He instantly slashes the valuations of cash-gushing, world-class clean energy infrastructure firms or established robotics leaders by 40%. The speed of the modern digital market only amplifies this manic-depression. Whether it's the 24/7 hype cycle of decentralized finance or the flood of capital into thematic remote-work ETFs, the herd is reacting strictly to mood swings, not to math. The secret isn't outsmarting the technology; it’s recognizing that the manic-depressive pricing engine hasn't changed a bit. You must wait patiently in the shadows for the inevitable panic, snatch up the gold beneath the gloss, and let time force the market back to reality.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "Document the Tantrum",
                                description: "The next time a world-class asset on your watchlist drops 10% in a week, open the your note. Write down the exact news headline that triggered the panic. Then, ask Caudex AI one question: \"Did this event permanently damage the cash-generating power of the business, or is Mr. Market just having a bad day?\" If it is merely a mood swing, you have found your target.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 252,
                currentProgress: 0.0
            ),
            3: CoreChapterContent(
                chapterNumber: 3,
                chapterTitle: "The Casino Chip Delusion",
                bookTitle: "The Little Book that Still Beats the Market",
                bookAuthor: "Joel Greenblatt",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Let me pull back the curtain on the greatest psychological trap in modern finance. In today’s hyper-connected, digital world, the financial industry has successfully gamified the accumulation of wealth. We swipe on glass screens, we tap glowing buttons, and we watch confetti explode when a trade executes. This frictionless environment has created a fatal, widespread delusion. People now treat stocks as nothing more than blinking ticker symbols or digital casino chips.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("They bet on a ticker because a social media influencer promised it was the next revolutionary disruption, entirely disconnected from what that ticker actually represents in the physical world. They stare at the jagged lines on a chart, completely blind to the fact that they are looking at real companies with factories, supply chains, employees, and cash registers. This massive disconnect is why the herd is constantly slaughtered when the market shifts. They panic when the line goes down and chase blindly when the line goes up because they have forgotten the foundational truth of the entire system: a stock is not a magical lottery ticket. It is a legally binding ownership percentage in a living, breathing, cash-generating enterprise. Until you rewire your brain to see the factory behind the flashing red and green numbers, you will always be a victim of the casino.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Math of the Schoolyard Hustle")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Joel Greenblatt cuts through Wall Street’s intentional complexity by ignoring complicated financial jargon and telling a simple story about a sixth-grade hustler named Jason. Jason runs a tight, highly profitable operation in his schoolyard. He buys packs of gum for 25 cents. With five sticks to a pack, Jason ruthlessly unloads individual sticks to his trapped classmates for 25 cents each. He pulls in $1.25 per pack, leaving him with $1 of pure, unadulterated profit every time he empties a wrapper.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Now, imagine Jason grows up and scales this operation into a wildly successful corporate chain called \"Jason’s Gum Shops\". He eventually wants to raise capital for himself, so he decides to divide his business into 1 million equal shares. He offers these shares to you for $12 apiece, valuing the entire business at $12 million.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("How do you know if $12 is a brilliant bargain or a massive rip-off? You ignore the stock price in isolation and look under the hood at the engine. Jason’s chain generated $10 million in sales last year. But sales aren't cash in your pocket. After paying $6 million for the actual gum , spending another $2 million on rent, employee wages, and electricity , and handing over 40% in taxes to the government , Jason’s Gum Shops produced exactly $1.2 million in cold, hard net income.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Because Jason divided the business into 1 million shares, each individual share is entitled to exactly $1.20 of that profit. If you pay $12 for a share that earns $1.20, your return on that investment in the first year is exactly 10%. Greenblatt calls this the \"Earnings Yield\". It is the ultimate truth serum. You then compare this 10% yield against the 6% return you could get risk-free from a 10-year U.S. government bond to decide if the risk is worth your capital. You aren't guessing where a stock chart is headed; you are calculating exactly how much cash your specific slice of the business generates relative to the price tag.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Modern Context: Piercing the Silicon Mirage")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Let’s drag this math out of the gum shop and into the modern arena. Right now, the market is absolutely obsessed with the infrastructure of tomorrow. We are witnessing massive waves of capital flowing into next-generation data centers, autonomous logistics networks, and clean energy conglomerates. The financial media will scream that a cloud-computing firm trading at $300 a share is a \"must-own\" asset simply because the digital revolution is inevitable.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But as a disciplined insider, you must brutally strip away the narrative and interrogate the engine. Let’s say this highly-hyped data center company has divided itself into 100 million shares, and last year it generated $50 million in actual profit. That means each share earned exactly $0.50. If Mr. Market is asking you to pay $300 for one of those shares, your Earnings Yield is a microscopic 0.16%.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Think about the sheer insanity of that math. You are taking on the massive, unpredictable risk of a highly competitive, capital-intensive technology sector to earn a fraction of a single percent in real cash generation. Meanwhile, a boring, guaranteed government bond is paying out significantly more. The crowd is so blinded by the story of remote work and cloud expansion that they are willingly stepping into a mathematical death trap. Mastering Earnings Yield gives you financial X-ray vision. It allows you to look at a hyped EV manufacturer, a new decentralized finance protocol, or a boring industrial supply company, and immediately calculate the actual cash reality underneath the gloss. It forces you to ask: \"Am I acquiring a cash-generating engine, or am I just overpaying for a cool story?\"")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is how you wire this cash-focused discipline into your routine tomorrow:")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The Yield Interrogation",
                                description: "Open the app’s screening tool and pull up the three most \"exciting\" companies on your current watchlist. Locate their \"Earnings Yield\" metric immediately. If that yield doesn't definitively crush the risk-free bond rate you established in Core 1, aggressively delete them from your watchlist. You do not fund bad math.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "Translate to Reality",
                                description: "Train your brain to stop looking at share prices in a vacuum. Tomorrow, whenever you view an asset inside the app, force yourself to verbally translate the price into a physical business transaction: \"I am paying $X to acquire exactly $Y of actual cash profit.\" If that sentence sounds like a terrible business deal in the real world, walk away.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 386,
                currentProgress: 0.0
            ),
            4: CoreChapterContent(
                chapterNumber: 4,
                chapterTitle: "The Steamroller and the Pennies",
                bookTitle: "The Little Book that Still Beats the Market",
                bookAuthor: "Joel Greenblatt",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Most retail investors fail because they step onto a battlefield where they are mathematically guaranteed to lose. When two companies announce a corporate merger, the target's stock price instantly jumps to just below the final acquisition price. The tiny gap left over is the \"arbitrage spread.\" Amateurs and overconfident professionals alike crowd into this space, risking massive amounts of capital to capture a tiny, fleeting percentage.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Chasing this spread today is the equivalent of picking up pennies in front of an algorithmic steamroller. In a chaotic market environment defined by intense regulatory scrutiny and high-frequency trading, traditional risk arbitrage is a fool's errand. You are competing against armies of specialized lawyers and heavily armed institutions. If the deal goes through, your reward is a tiny sliver of profit. But if the deal breaks due to a financing glitch, a regulatory block, or a macro-economic shock, the stock collapses and your capital is obliterated. It is an asymmetrical trap.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Sinkhole and the Sweetener")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author learned this lesson the hard way during the acquisition of Florida Cypress Gardens by Harcourt Brace Jovanovich. It looked like a flawless, guaranteed deal. The financing was secure, the shareholder votes were locked up, and the synergy was obvious.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Then, the earth literally opened up. A massive sinkhole swallowed the theme park's main pavilion just weeks before the closing date. The deal was thrown into jeopardy, and the author found himself staring at a potential $6.42 loss just to capture an 80-cent profit. The lesson was brutal and clear: \"Don't try this at home.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The brilliant alternative is to completely ignore the merger spread and focus exclusively on \"Merger Securities.\" Sometimes, to win a bidding war, an acquiring company exhausts its cash and common stock, forcing it to pay target shareholders with strange \"sweeteners\". These can take the form of preferred stock, warrants, or complex contingent value rights (CVRs).")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("When these bizarre securities are distributed, absolute chaos ensues. The author paints a picture of an investor who owned an agriculture stock, suddenly receiving complex 9% bonds due in 2010. They do not want them. More importantly, the massive institutional funds cannot keep them. Because a mutual fund manager is strictly mandated to own equities, their compliance department legally forbids them from retaining bizarre debt instruments or warrants. Both the amateur and the professional have the exact same reaction: they blindly unload the new securities as fast as humanly possible, entirely ignoring their fundamental value.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Algorithmic Blind Spot")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, this dynamic is exacerbated by the sheer velocity of automated passive investing. Consider a massive, traditional infrastructure company getting acquired by a dominant force in the Data Center and Artificial Intelligence space. To close the deal, the acquirer pays in cash, plus a complex \"warrant\" tied to the future completion of a new Clean Energy microgrid.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("When the deal finalizes, the traditional infrastructure ETFs and conservative dividend funds receive millions of these Clean Energy warrants. The tracking algorithms driving these ETFs are incapable of processing this anomaly. The warrants do not fit the sector mandate, they lack historical volatility data, and they pay no current yield.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The machine's only programmed response is to immediately purge the unrecognized asset from the portfolio. This creates a spectacular, localized crash in the price of the warrants. While the financial media hyperventilates over the size of the overall merger, you are waiting in the shadows. You can scoop up these orphaned instruments for pennies on the dollar, capitalizing entirely on the fact that an algorithm was forced to throw them in the trash.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "Command the Deep Research Agent",
                                description: "Tomorrow, configure your iOS app to scan the \"Live News Feed\" for complex acquisitions involving multiple payment methods. Deploy your AI agent to audit the SEC proxy statement specifically for the words \"warrants,\" \"preferred,\" or \"contingent value rights.\"",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "Ignore the Spread",
                                description: "When a major acquisition is announced, absolutely refuse to participate in the traditional arbitrage game. Leave the tiny, dangerous percentage gains to the institutional steamrollers.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "Capture the Purge",
                                description: "Wait patiently for the merger to finalize and the strange \"sweeteners\" to be distributed. Let the institutional funds and rigid ETFs blindly liquidate their un-mandated assets. Once the liquidation pressure breaks the price, move in and accumulate the discarded securities at a steep discount to their intrinsic value.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 289,
                currentProgress: 0.0
            ),
            5: CoreChapterContent(
                chapterNumber: 5,
                chapterTitle: "The Spreadsheet Delusion",
                bookTitle: "The Little Book that Still Beats the Market",
                bookAuthor: "Joel Greenblatt",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Have you ever noticed how the smartest people in the room often make the most disastrous financial decisions? The friction comes from a fundamental misunderstanding: we treat finance like it is physics. We believe it operates on immutable laws, formulas, and perfectly rational answers. We assume that if we just build a complex enough system—perhaps engineering intricate data architecture to process market trends and chart the future—we can perfectly conquer the market.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But human beings aren't code. We fail because we optimize for a spreadsheet instead of optimizing for a peaceful night's sleep. When you design a flawless financial plan on a quiet Sunday afternoon, you are acting rationally. When the market drops 15% on a Tuesday morning and your heart starts pounding, rationality evaporates. The perfectly calculated plan shatters against the reality of human emotion. The secret the industry tries to hide is that trying to be completely, coldly rational is actually a trap.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Fever of the Era")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author tells a compelling story about how our personal history dictates our financial reality. Imagine two different people. One was born in 1950 and watched the stock market go essentially nowhere during their formative years in the 1970s. The other was born in 1970 and came of age during the unstoppable roaring bull market of the 1990s.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("If you ask both of them about risk, you will get two entirely opposite answers. And the fascinating part? No one is crazy. The author points out that every financial decision makes perfect sense to the person making it at that exact moment, heavily filtered through the tiny sliver of economic history they personally experienced. We are all viewing money through a highly distorted, individualized lens.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Because our lenses are so biased, the author argues that aiming for strict rationality is a vulnerability. A purely rational investor might look at historical data and decide to leverage themselves to the absolute limit because the expected return is mathematically positive. But a reasonable investor knows that if a sudden dip causes a margin call, or simply triggers enough anxiety to force a panic exit, the math is entirely useless. You must aim for a strategy that is reasonable enough to let you stay in the game long-term, even if it isn't flawlessly rational on paper.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("Trading the Algorithm for the Human")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Let’s drag this into the reality of right now. We are living in an era of overwhelming technological acceleration. AI infrastructure is booming, data centers are eating massive amounts of capital, and markets swing wildly based on geopolitical murmurs or the sudden movement of a single crypto whale.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, you hold an unprecedented amount of analytical power right in your pocket. You can deploy an autonomous Deep Research Agent to parse a decade of dense 10-K filings in minutes, and it might tell you exactly what you need to know about a company. The AI feels no fear. It doesn't sweat. It only sees pure expected value and cold, hard rationality. But you are the one who has to stare at the screen when a sudden supply chain shock causes those exact companies to plummet 20% in a week. When you look at your phone and see those red numbers flashing across your live widget, the psychological pain immediately overrides the AI's logical blueprint.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In today's world of frictionless, hyper-fast mobile finance, the ultimate edge isn't just having the most sophisticated data or the fastest alert system. It is building a strategy that actively accommodates your own human flaws. If maintaining a theoretically \"perfect\" portfolio keeps you staring at the ceiling at 2 AM, it is a catastrophic strategy for you. Wealth isn't built by the person with the smartest data; it is built by the person who creates a framework reasonable enough to survive their own worst impulses.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "Audit Your Sleep Factor",
                                description: "Open your Tracking tab and review your Watchlist. Look at those assets and ask yourself a ruthless question: \"If the market drops 20% tomorrow, will staring at these red numbers cause me to panic?\" Adjust your exposure until you can sleep.Adjust your exposure until the answer is a definitive \"no.\"",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 279,
                currentProgress: 0.0
            ),
        ],
        10: [
            1: CoreChapterContent(
                chapterNumber: 1,
                chapterTitle: "The Illusion of the Crowded Room",
                bookTitle: "The Most Important Thing",
                bookAuthor: "Howard Marks",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is the uncomfortable truth most retail investors never figure out: the market is a giant, ruthlessly efficient weighing machine of everyone else's opinions. You log on, read the morning headlines, and see a universally praised trend. It feels safe to agree. It feels logical. But here is the friction: if everyone already knows a piece of information, it is already baked into the price. You cannot gain an edge by knowing what everyone else knows and thinking how everyone else thinks. When you agree with the consensus, you are guaranteeing yourself average returns. And in the financial world, average returns—after taxes, fees, and inflation—equate to a slow, silent bleed of your wealth. To actually win, you have to be willing to be profoundly uncomfortable. You have to stand outside the crowded room.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Second-Level Thinker")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In The Most Important Thing, Howard Marks illustrates this with a simple, devastating concept that separates the amateurs from the masters. He calls it \"Second-Level Thinking.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Imagine a widely celebrated tech company that just announced record profits. The first-level thinker looks at the news and says, \"This is a great company with massive growth; let's allocate capital.\" It is simple, reactive, and entirely wrong.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Marks asks us to look deeper. The second-level thinker looks at the exact same company and says, \"It is a good company, but everyone thinks it is a great company, and it is priced as if it is a perfect company. Therefore, it is overpriced and mathematically dangerous.\" The story here isn't about finding a good business; it's about understanding the gap between perception and reality. First-level thinking relies on intuition; second-level thinking relies on complex, divergent logic. You have to constantly ask: Who doesn't know this yet? And what is the crowd getting wrong?")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The AI Echo Chamber")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Let’s drag this into today's reality. Open any financial feed, and you will see alerts like \"Tech Stocks Rally on Strong AI Earnings\" screaming at you. First-level thinking looks at the massive cloud infrastructure providers or the leading GPU designers and assumes they are the only path to the future. The crowd is entirely obsessed with the obvious giants.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But a second-level thinker realizes those obvious trades are already saturated by the masses. Instead of fighting for scraps at the top, they go deeper into the data. They might run a sophisticated cluster analysis algorithm across thousands of equities, deliberately searching for the hidden, undervalued companies that actually power the revolution—the obscure utility companies providing the massive energy required for those data centers, or the specialized manufacturers building server cooling systems. The crowd is staring directly at the shiny software; the second-level thinker is quietly mining the numbers to accumulate the unglamorous infrastructure the software desperately depends on. The edge isn't in predicting the obvious wave; the edge is finding the neglected assets the wave will eventually lift.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "Hunt the Derivatives",
                                description: "Stop looking at the primary players in a hot sector. Map out the supply chain. Identify one obscure, secondary company that provides the \"picks and shovels\" to the current gold rush.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "Audit Your Agreement",
                                description: "Review your current tracking list. If you cannot articulate a clear, uncomfortable reason why your thesis disagrees with the broader market on a specific asset, think again!",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 220,
                currentProgress: 0.0
            ),
            2: CoreChapterContent(
                chapterNumber: 2,
                chapterTitle: "The Perfect Machine Fallacy",
                bookTitle: "The Most Important Thing",
                bookAuthor: "Howard Marks",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Most people walk into the financial markets believing a very dangerous academic lie: that the market is a perfectly calibrated machine. They assume that because millions of supercomputers, analysts, and institutional algorithms are constantly crunching data, the price you see on the screen is exactly what an asset is worth at that exact second. This creates a paralyzing friction. If the market is perfectly efficient, why bother analyzing anything at all? You might as well just index your money and walk away. Or worse, when a stock is plunging, retail investors assume the \"smart money\" must know some catastrophic secret, so they panic and follow the crowd out the door. They fail because they outsource their conviction to the current price tag, completely forgetting that the price tag is ultimately set by deeply flawed, highly emotional human beings.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Hundred-Dollar Bill on the Sidewalk")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In The Most Important Thing, Howard Marks destroys this myth of perfect efficiency with a classic industry joke. A strict academic finance professor and his student are walking down the street. The student looks down, points, and says, \"Look, a hundred-dollar bill on the sidewalk!\" The professor doesn't even break his stride to look. \"Don't bother,\" he replies. \"If it were a real hundred-dollar bill, someone else would have already picked it up.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The professor is blinded by his own rigid theory. Marks uses this to illustrate a profound truth: the market is usually efficient, but it is not always efficient. Because it is driven by human fear and human greed, the pendulum of sentiment swings much too far in both directions. In moments of panic, investors indiscriminately dump brilliant businesses at bargain prices. In moments of euphoric mania, they pay absurd premiums for absolute garbage. Your entire job as a strategist is to wander the sidewalks during these rare moments of emotional extremism and calmly pick up the hundred-dollar bills that everyone else is too terrified or too distracted to claim.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Index Fund Avalanche")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Let’s apply this to the madness of the modern tape. Today, massive market failures are happening in plain sight, entirely manufactured by the rise of passive ETFs and algorithmic trading. When a trillion dollars blindly flows into an S&P 500 index fund, that capital is forced to automatically allocate into the largest tech companies, regardless of their actual underlying valuation or fundamental reality. This creates a self-fulfilling momentum bubble at the very top.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But here is the secret: this passive avalanche leaves the rest of the market completely starved of capital and attention. Boring, highly profitable mid-cap companies—perhaps a regional logistics firm or a niche industrial supplier—are completely ignored simply because they aren't part of the passive mega-trend. Add in the wild, narrative-driven swings of Crypto, where billions in market cap are shifted by a single late-night tweet, and you have a landscape absolutely ripe with inefficiency. The market routinely fails to price neglected assets correctly. That failure is your precise entry point.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "Deploy the Contrarian Agent",
                                description: "Find a company that recently suffered a massive, headline-driven price drop. Run a Deep Research report. Focus exclusively on the generated \"Pros and Cons\" list and \"Competitive Moat\" modules to determine if the core business survived the panic.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "Track the Quiet Money",
                                description: "Ignore the noisy retail crowd. Navigate to the Whales tab and filter for Hedge Fund activity. Watch what the silent, institutional capital is secretly accumulating while the public is distracted by the latest tech rally. See what they do, not what they say!",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 236,
                currentProgress: 0.0
            ),
            3: CoreChapterContent(
                chapterNumber: 3,
                chapterTitle: "The Fatal Halo Effect",
                bookTitle: "The Most Important Thing",
                bookAuthor: "Howard Marks",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Most people conflate a great company with a great investment. It is the most expensive mistake in finance. You see a business with a brilliant CEO, flawless products, and massive revenue growth, and your brain instantly short-circuits to, \"I need to own a piece of this.\" But you are forgetting the invisible, unforgiving second variable. A world-class company can be a toxic, wealth-destroying asset if the price tag is wrong. This friction happens because we are biologically wired to seek out quality and safety, but we are rarely trained to calculate the mathematical relationship between cost and intrinsic worth.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Nifty Fifty Mirage")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In The Most Important Thing, Howard Marks illustrates this trap by pointing back to the \"Nifty Fifty\" era of the late 1960s. The author tells the story of a time when institutional investors became blindly obsessed with fifty dominant, seemingly invincible corporations—companies like IBM, Xerox, and Kodak. The consensus was that these businesses were so exceptionally good, the price you paid for them simply didn't matter. They were \"one-decision\" assets: you acquire them and never let go.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But Marks points out the catastrophic flaw in that logic. Because everyone universally agreed they were flawless, their prices were bid up to astronomical, euphoric multiples. When the reality of the 1970s market crash hit, those investors were decimated. The companies themselves mostly survived, and many continued to grow their underlying revenues, but the investors still lost fortunes. Why? Because, as Marks coldly reminds us, there is no such thing as a good asset regardless of price. Value is what the business generates in cash; price is what you surrender to get it. When price aggressively disconnects from value, the risk of permanent loss becomes absolute.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Price of the AI Future")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Look out the window at today's landscape. We are living through an exact replica of this divergence with Artificial Intelligence and the massive build-out of data centers. You don't need a PhD to know that AI is fundamentally rewiring the global economy. The narrative is undeniable. But when an AI infrastructure stock is trading at eighty times its forward revenue, the market is pricing in a future that requires absolute, uninterrupted perfection for the next two decades.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Even if that company radically succeeds and dominates the cloud sector, your personal return on investment might be zero or deeply negative because you already paid for all of that future success upfront. The modern trap is finding a genuinely disruptive, world-changing technology, but paying a price that mathematically guarantees a loss.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "Force the Fundamental View",
                                description: "When looking at an asset's chart, ignore purely speculative technical indicators. Instead, enable permitted overlays like Historical P/E Ratios or \"Fair Value\" Corridors to see if the current price has completely severed ties with historical reality.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "Interrogate the AI",
                                description: "Open the Wiser tab, which serves as your education hub. Use the interactive chat functionality to ask the Caudex agent directly: \"Is this company's current valuation justified by its historical cash flow?\". Force the system to strip away the narrative.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 204,
                currentProgress: 0.0
            ),
            4: CoreChapterContent(
                chapterNumber: 4,
                chapterTitle: "The Volatility Illusion",
                bookTitle: "The Most Important Thing",
                bookAuthor: "Howard Marks",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The financial industry has brainwashed you with a mathematical lie. They desperately want you to believe that \"risk\" simply means a bouncy chart. If an asset's price swings wildly from day to day, they label it dangerous. If it moves in a slow, predictable, almost flat line, they label it safe. This is exactly why most retail investors fail: they are completely terrified of turbulence. When they hit an air pocket and their portfolio drops 20% in a week, they vomit up their positions in sheer panic. They believe they are strictly managing risk, but they are actually just converting temporary paper turbulence into a permanent, unrecoverable destruction of their own wealth. They survive the bumpy flight by jumping out of the plane.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Mirage of the Sharpe Ratio")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In The Most Important Thing, Howard Marks completely dismantles this academic definition of risk. The author tells the story of how finance professors and Wall Street institutions fell deeply in love with \"volatility\" as a metric simply because it can be easily measured and plugged into an elegant spreadsheet.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But Marks argues that true risk cannot be quantified by past price wiggles. Risk, in the real world, is one thing and one thing only: the probability of a permanent loss of capital.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Imagine driving over two bridges. Bridge A is a shaky, wooden suspension bridge that sways violently in the wind, but its engineering is fundamentally sound and it will definitely get you across the gorge. Bridge B is a remarkably smooth, freshly paved concrete highway that abruptly ends in a sheer drop off a cliff. Academic finance tells you the wooden bridge is \"risky\" because it moves. Marks tells you the smooth highway is the ultimate danger because it leads to a permanent zero. Volatility is simply the emotional toll you pay to cross the bridge; permanent loss is failing to reach the other side.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("Surviving the Crypto Winter")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Look at the brutal reality of the modern cryptocurrency markets, specifically foundational assets like Bitcoin. If you measure risk purely by volatility, it appears to be one of the most terrifying assets in human history, routinely suffering 60% to 80% drawdowns. The first-level thinker screams that it is too dangerous to touch.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But what about the traditional investor who acquired \"safe\" long-term government bonds in 2020? Those bonds barely bounced around day-to-day, but thanks to explosive inflation and rapidly rising interest rates, they inflicted a slow, silent, and permanent destruction of purchasing power. The bondholders experienced almost zero volatility, yet suffered a catastrophic loss of real wealth. Today, the greatest danger isn't acquiring something that bounces violently; it's acquiring a structurally flawed asset—like a dying legacy tech company or an overpriced bond—that goes down slowly and simply never comes back.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "Interrogate the Bear Case",
                                description: "Open the Research tab and check your report. Scroll directly to the visual Bear Case module in the Core Thesis to read. Also, look at the \"Critical Factors to Watch\" (like rising debt levels or a credit agency downgradem,..) and ask yourself if any of these specific threats could trigger a permanent collapse.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 209,
                currentProgress: 0.0
            ),
            5: CoreChapterContent(
                chapterNumber: 5,
                chapterTitle: "The Illusion of the Risk-Reward Contract",
                bookTitle: "The Most Important Thing",
                bookAuthor: "Howard Marks",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is the silent trap most investors fall into: they treat risk like a binding contract. They look at a highly speculative asset and tell themselves, \"If I am just brave enough to stomach the danger, the market mathematically owes me a massive payout.\" They view risk as a tollbooth—you pay with your peace of mind, and in exchange, you get handed a higher return.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But the market does not reward bravery, and it certainly does not owe you a premium just because you took a reckless gamble. If exposing your capital to a high probability of destruction actually guaranteed a higher return, it wouldn't be risky in the first place—it would just be a high-yield savings account. The friction here is that people conflate expected return with guaranteed outcome. They engineer their portfolios assuming the best-case scenario is a certainty, completely forgetting that risk isn't a volume knob you turn up to get richer. It is the very real, mathematical probability that your capital gets permanently wiped out. You do not get paid for taking risk. You only get paid if the risk happens to spare you.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Widening Bell Curve")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Howard Marks aggressively corrects this fatal misunderstanding. He tells us to throw away the straight line and visualize risk as a bell curve of possible outcomes. As you take on more risk, the expected return might move slightly higher, but the bell curve dramatically flattens and widens. Most importantly, the left tail of that curve stretches deep into the territory of permanent, catastrophic loss.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author stresses that superior investors do not succeed by possessing a magical crystal ball that predicts the future. They succeed through absolute \"risk control.\" They actively construct portfolios that can survive scenarios they didn't see coming. Marks leverages the concept of alternative histories—the invisible timelines of what could have happened. Just because you drove a car blindfolded and didn't crash doesn't mean it was a brilliant strategy; it means you got lucky in this specific timeline. The master investor demands a massive \"risk premium\" upfront—acquiring an asset at a heavily discounted price—to insure against the inevitable moments when the worst possible alternative history suddenly becomes reality.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Geopolitical Blind Spot")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Look at the current euphoria surrounding the Artificial Intelligence super-cycle. Retail capital is flooding into a handful of GPU manufacturers and cloud providers, pricing them for absolute perfection. The crowd assumes the only risk is temporary turbulence. But they are entirely blind to the catastrophic left tail of the curve.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("What happens if a sudden geopolitical trade war blockades the specific semiconductor foundries that manufacture 90% of these advanced chips? What if a global regulatory framework instantly criminalizes the massive data scraping techniques required to train these language models? If your portfolio only survives if the perfect, uninterrupted AI future plays out, you haven't controlled your risk; you have simply closed your eyes. Insuring against the unknown means recognizing that black swans are entirely unpredictable by definition. You must structure your capital allocation so that a geopolitical shock or a sudden regulatory hammer in the tech sector doesn't permanently wipe you out. You don't build a storm bunker because you know exactly when the tornado is coming; you build it because you acknowledge you cannot control the weather.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "Audit Your Firewall",
                                description: "Navigate to the Tracking tab and check your \"Diversification Score\" widget. You are not trying to perfectly smooth out your daily chart; you are checking your sector allocation to ensure that a localized failure in one industry doesn't result in total, irrecoverable ruin.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 240,
                currentProgress: 0.0
            ),
            6: CoreChapterContent(
                chapterNumber: 6,
                chapterTitle: "The Crystal Ball Delusion",
                bookTitle: "The Most Important Thing",
                bookAuthor: "Howard Marks",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Humans are desperately uncomfortable with uncertainty, which is why the financial industry makes billions selling the illusion of foresight. You turn on the television, and a very confident analyst in a tailored suit tells you exactly where the S&P 500 will be in six months, or precisely what month the Federal Reserve will pivot. The friction here is that macroeconomic forecasting is essentially a coin toss dressed up as science. Amateurs fail because they build their entire financial architecture on a rigid prediction of a future they cannot possibly know. When their prediction inevitably misses the mark, their portfolio shatters. They waste all their analytical energy trying to guess what will happen next, instead of doing the much harder work of figuring out what is happening right now.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Temperature of the Room")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In The Most Important Thing, Howard Marks aggressively dismisses the value of macro-forecasting. He offers a liberating, ruthlessly pragmatic alternative: we never know where we are going, but we ought to know exactly where we are.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Imagine you are deciding how to dress for the day. You don't need a meteorologist to swear on their life that it will rain at exactly 2:15 PM next Tuesday. You just need the situational awareness to look out the window and check the thermometer today. If the sky is bruised and the air is heavy, you carry an umbrella. Marks uses the metaphor of the pendulum—a cycle that swings inevitably from extreme optimism and underpriced risk, to extreme pessimism and overpriced risk. You do not need to predict the exact day the pendulum will reverse course; you simply need to measure how far it has swung. When credit is cheap, deals are absurdly leveraged, and your neighbor is suddenly an expert on initial public offerings, the pendulum has swung to dangerous euphoria. You don't predict the crash; you simply acknowledge the temperature of the room and adjust your exposure because the environment is mathematically hostile.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Zero-Interest Hangover")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Let’s map this reality onto the modern tape. For over a decade, we surfed a bizarre, artificial tide of zero-percent interest rates. Money was practically free. That specific macro tide lifted absolutely everything—from profitless tech startups to speculative NFTs. A whole generation of investors mistook a rising macro tide for their own financial genius.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But then the pendulum violently swung. Central banks cranked rates at a historic pace to fight inflation, and the tide rapidly went out. The companies that relied entirely on cheap debt to survive were suddenly swimming naked. Today, we are surfing a new tide defined by the massive, capital-intensive infrastructure build-out for Artificial Intelligence and a shifting geopolitical supply chain. The first-level thinker is still sitting around trying to predict if the Fed will adjust rates by 25 basis points next quarter. The master strategist isn't guessing; they are simply observing that the cost of capital has fundamentally changed. They are quietly rotating away from legacy businesses dependent on cheap debt, favoring companies with fortified balance sheets that can self-fund their own data center expansions.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "Check the Macro Weather",
                                description: "Open the Tracking tab and review your \"Alerts & Upcoming Events\" section. Do not try to predict the upcoming macroeconomic events. Instead, observe what the consensus expects and stress-test your portfolio against the exact opposite outcome.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "Audit the Debt Reality",
                                description: "Open the report for your highest-conviction asset. Bypass the growth narrative for now and expand the Macro-Economic & Geopolitical module. Ensure the company is fundamentally positioned to survive in the current interest rate and geopolitical environment, not the one from three years ago.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 240,
                currentProgress: 0.0
            ),
            7: CoreChapterContent(
                chapterNumber: 7,
                chapterTitle: "Combating Emotional Gravity",
                bookTitle: "The Most Important Thing",
                bookAuthor: "Howard Marks",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Gravity of the Crowd")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The greatest threat to your financial survival is not the Federal Reserve, a market crash, or a geopolitical crisis. It is your own biology. We are herd animals, hardwired over millennia to find safety in numbers. If you saw the rest of your tribe running in terror, your instincts demanded you run too, without stopping to ask why. That instinct kept your ancestors alive, but in the modern financial markets, it is a wealth-destroying disease.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The friction here is that the math of wealth creation requires you to act completely unnaturally. When the market plunges into a sea of red, the financial pain registers in your brain exactly like physical pain. Your amygdala—the fear center of your brain—hijacks your logic and screams for you to escape. Conversely, when everyone around you is getting rich on a speculative bubble, the fear of missing out acts as an irresistible gravitational pull. Most investors fail not because they lack intelligence or data, but because they lack the emotional shock absorbers required to withstand the gravity of a panicking crowd.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Pendulum of Expectations")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author, John Bogle, understood that investing is ultimately an exercise in psychological endurance. To combat this emotional gravity, he introduced the metaphor of the \"Emotional Pendulum.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Bogle teaches us to look at the history of the stock market as a tale of two different realities. The first reality is the business itself—the \"Real Market.\" This is the actual dividend yield and earnings growth of the corporations you own. This reality is remarkably stable; it slowly and steadily marches upward over the decades.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The second reality is the \"Expectations Market.\" This is the speculative return, driven entirely by what people are willing to pay for those earnings. This is the pendulum. It swings wildly from irrational exuberance (when investors view the future through rose-colored glasses) to unjustified pessimism (when they believe the sky is falling). Bogle tells us that while the pendulum swings violently from side to side, generating terrifying headlines and euphoric manias, it always, eventually, rests at the center—anchored to the true economic value of the businesses. The insider secret is realizing that the swings of the pendulum are an illusion. If you anchor your emotions to the \"Real Market,\" the wild swings of the crowd lose their power over you.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Modern Context: The Weaponized Panic Machine")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In Bogle's era, the pendulum swung slowly. You had to wait for the morning newspaper to feel the panic. Today, emotional gravity has been weaponized by technology, and the pendulum swings at the speed of light.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("We live in a financial ecosystem engineered to harvest your attention by spiking your cortisol. Consider a sudden escalation in a Trade War or a rumored regulatory crackdown on Crypto. Within seconds, algorithms push catastrophic headlines to your lock screen. Social media \"finfluencers\" broadcast emergency updates with apocalyptic thumbnails. The screens on your zero-commission trading apps flash aggressively in red, triggering a physiological stress response.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("This applies perfectly to the hyper-volatile sectors of today. Look at Data Center and AI infrastructure companies. The underlying businesses are growing steadily—building facilities and laying cables. That is the Real Market. But the stocks swing 30% in a single week based on a whispered rumor about a delayed microchip. When you let the gamified alerts and the 24/7 news cycle dictate your mood, you are tying your emotional state to a pendulum being violently pushed by high-frequency trading bots and engagement-hungry media companies. Your only true edge in the modern era is emotional detachment. You win by building a psychological fortress that the noise cannot penetrate.")
                    ),
                ],
                audioDurationSeconds: 243,
                currentProgress: 0.0
            ),
            8: CoreChapterContent(
                chapterNumber: 8,
                chapterTitle: "The Illusion of Safety in Numbers",
                bookTitle: "The Most Important Thing",
                bookAuthor: "Howard Marks",
                sections: [
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("There is a paradox in investing that breaks the brains of most beginners: The safer an investment feels, the more dangerous it actually is.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In normal life, consensus is a great filter. If a thousand people say a restaurant is terrible, you don’t eat there. If everyone agrees a specific car is reliable, you buy it. But the stock market is a mirror universe. By the time everyone agrees that a specific technology or company is the undeniable future, the price of that asset has already been bid up to the stratosphere. The friction here is that you want to buy the \"obvious\" winner, but you forget that the stock market operates as an auction. When you buy what is universally loved, you pay a massive premium. I call this the \"Comfort Tax.\" Most people fail because they think they are buying a great business, when in reality, they are just buying a great consensus—and paying top dollar for it.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("Standing in Front of the Pendulum")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author, John Bogle, understood this psychological warfare intimately. He frequently leaned on the metaphor of the pendulum to describe the manic-depressive nature of the stock market.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He tells us that the investment markets swing like a giant pendulum. At the exact center of the arc lies intrinsic value—the boring, mathematical reality of corporate earnings and dividends. But the pendulum rarely rests there. It is constantly swinging outward toward the extremes of irrational exuberance, fueled by greed, and then violently crashing back toward the extremes of unjustified pessimism, fueled by terror.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Bogle reminds us of the tragic story of Sir Isaac Newton, one of the smartest humans to ever live. Newton invested in the South Sea Company during a massive speculative bubble. He initially made a fortune, exited, but then—watching his friends get richer as the pendulum swung further into euphoria—he abandoned his logic, jumped back in at the peak, and lost his life savings. Newton famously lamented, \"I can calculate the motions of the heavenly bodies, but not the madness of the people.\" Bogle’s lesson is that intelligence cannot save you from the pendulum. Only aggressive disagreement can. When the pendulum is at its highest, most euphoric arc, you must stubbornly disagree with the optimism. When it swings to the darkest depths of despair, you must stubbornly disagree with the doom.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Modern Context: The Algorithmic Echo Chamber")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, disagreeing with the crowd is ten times harder because the crowd now lives in your pocket, amplified by algorithmic echo chambers.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Think about the recent frenzies surrounding Crypto \"super-cycles\" or the parabolic rise of AI and Data Center infrastructure. The modern pendulum isn't just pushed by human emotion; it is accelerated by social media feeds, Reddit communities, and gamified apps that curate a reality where everyone is making money except you. When you open an app and see a wall of green, and every influencer is predicting a \"new paradigm\" for Clean Energy or Remote Work tech, the algorithmic consensus feels like an undeniable fact.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The trap is that the modern financial ecosystem punishes contrarians. If you disagree with a meme-stock rally, you are ridiculed. If you doubt the immediate profitability of a massive AI rollout, you are called a dinosaur. The modern pendulum swings faster and hits harder. To survive it, you have to realize that when your curated feed is uniformly euphoric about a sector, the pendulum has reached its maximum extension. The risk is highest exactly when the crowd feels the safest. You must learn to view overwhelming consensus not as a green light, but as a blinding siren warning you to step back.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Devil’s Advocate\" Prompt",
                                description: "Whenever you feel absolute certainty that a popular stock is a guaranteed winner, open your Chat with Caudex. The Instruction: Type exactly this: \"Give me the most brutal, pessimistic bear case for [Ticker]. Tell me exactly how and why this company will fail.\" Force the AI to aggressively disagree with your optimism before you make a decision.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 269,
                currentProgress: 0.0
            ),
            9: CoreChapterContent(
                chapterNumber: 9,
                chapterTitle: "Scavenging for Neglected Value",
                bookTitle: "The Most Important Thing",
                bookAuthor: "Howard Marks",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Glamour Trap")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("There is a dangerous assumption that a \"world-changing company\" automatically makes for a \"great investment.\" The friction here is that the stock market does not reward you for identifying what is brilliant; it rewards you for identifying what is mispriced.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Most people fail because their ego demands they invest in the glamorous, the revolutionary, and the shiny. They want to brag at dinner parties about owning the company curing a disease or building the fastest microchip. It is incredibly tempting to build complex data mining models, running sophisticated cluster analyses to hunt for perfectly mispriced anomalies hiding in the shadows. But the brutal truth of the market is that glamour is expensive. The crowd bids up the price of \"revolutionary\" companies until the math guarantees a mediocre return. True wealth isn't found in the spotlight; it is scavenged in the neglected, boring, and unsexy corners of the market that everyone else is too impatient to analyze.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Triumph of the Tortoise")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author, John Bogle, solved this puzzle by dissecting exactly where stock market money comes from. He revealed that your total return is made of two entirely different beasts: \"Speculative Return\" and \"Fundamental Return.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Speculative Return is the hype. It is the change in the Price-to-Earnings (P/E) ratio based on human emotion. It is the hare—leaping forward one year, collapsing in a panic the next.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Fundamental Return is the neglected value. It consists of just two boring numbers: the initial dividend yield and the company's actual earnings growth. This is the tortoise. It is unsexy. It doesn't generate breaking news alerts. But Bogle points out a staggering historical fact: over the long span of market history, Speculative Return effectively cancels itself out to zero. The booms and busts net out. Therefore, virtually 100% of the wealth created in the stock market over the last century came from the tortoise—the quiet, neglected, compounding cash flows of boring businesses. The insider secret is to stop obsessing over the unpredictable hare of speculation and start scavenging for the relentless tortoise of fundamental value.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Modern Context: The Unsexy Backbone")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In today’s hyper-financialized world, the obsession with the \"hare\" has reached a fever pitch.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Look at the current landscape. Millions of investors are throwing capital into purely speculative assets like Crypto meme coins, which have zero earnings, zero dividends, and zero fundamental value. It is 100% Speculative Return—a pure gamble on the next person's willingness to pay more. Or look at the AI frenzy. Everyone is desperately trying to identify the next flashy software startup that will disrupt the world.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("But where is the neglected value? It is in the unsexy backbone. While the crowd fights over the AI software companies trading at 100 times their earnings, the neglected value is in the boring industrial utility companies that have to supply the massive electrical power to the new Data Centers. It is in the unglamorous copper miners providing the wiring for the Clean Energy grid. These companies often pay steady dividends and grow their earnings predictably, but they don't get trending hashtags. In a market obsessed with disrupting the future, your greatest edge is finding the quiet, highly profitable companies that are simply selling the shovels for the gold rush.")
                    ),
                ],
                audioDurationSeconds: 218,
                currentProgress: 0.0
            ),
            10: CoreChapterContent(
                chapterNumber: 10,
                chapterTitle: "The Discipline of Inaction",
                bookTitle: "The Most Important Thing",
                bookAuthor: "Howard Marks",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Myth of the Hustle")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("There is a toxic lie baked into our culture that dictates how we measure success: we worship the \"hustle.\" From the moment you enter the workforce, you are trained that activity equals progress. If you want to get promoted, you work longer hours. If you want to grow a business, you launch more products. We are conditioned to believe that motion is the only antidote to failure.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("When you bring this \"hustle\" mentality into the financial markets, it becomes a weapon of mass wealth destruction. The friction here is that your brain equates \"doing nothing\" with laziness and negligence. When the market opens, you feel an overwhelming compulsion to scour the news, run screeners, and execute trades. You feel like you must have an opinion on every macroeconomic event, every earnings report, and every geopolitical crisis. Most people fail at investing because they treat it like a 9-to-5 job where they are paid piecework. They believe that if they are not actively managing, tweaking, and reacting, they are falling behind. The hardest insider secret to accept is that the stock market is the only domain on earth where extreme, bordering-on-lazy inactivity is the ultimate competitive advantage.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Umpire Without a Whistle")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author addresses this psychological trap by borrowing a masterclass concept from Warren Buffett, who in turn borrowed it from the greatest baseball hitter of all time, Ted Williams.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In his book The Science of Hitting, Ted Williams carved the strike zone into 77 distinct cells. He calculated that if he only swung at pitches in his absolute \"sweet spot\" (the center cells), he would bat .400. If he reached for pitches on the lower outside corner, his average would plummet to .230. His secret was not a faster swing; it was the ruthless discipline of inaction. He aggressively waited for the \"fat pitch.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author translates this to the financial world with one massive, beautiful advantage: In the stock market, there are no \"called strikes.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Imagine standing at home plate. The pitcher is Wall Street. He throws a pitch called \"Brazilian Tech Startups.\" It zips by. You don't understand it. In baseball, the umpire yells \"Strike one!\" But in the stock market, the umpire is silent. The pitcher throws \"European Bank Mergers.\" You let it pass. No penalty. You can stand at the plate for five years, bat resting comfortably on your shoulder, watching tens of thousands of pitches sail by. You only lose the game when your ego forces you to swing at a pitch you don't understand just because the crowd is cheering. The true master acknowledges exactly what they don't know, builds a tiny circle of competence, and practices \"patient opportunism\"—waiting years, if necessary, for the single, undeniable fat pitch where the odds are overwhelmingly in their favor.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Modern Context: The 24/7 Pitching Machine")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, the discipline of inaction is exponentially harder because you are no longer standing in a quiet baseball stadium; you are trapped inside a neon-lit, 24/7 pitching machine that never unplugs.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The pitches are coming at 100 miles per hour, directly to your lock screen. A new Crypto protocol launches, and your feed explodes with stories of teenagers becoming millionaires overnight. The machine throws a \"Generative AI\" pitch. Then a \"Space Exploration\" pitch. Then a \"Work-from-Home\" disruptor pitch.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The gamification of modern finance is designed to make you feel stupid for keeping your bat on your shoulder. When a meme stock surges 400% in a week, the algorithmic echo chamber mocks your discipline. The modern trap is the belief that because you have access to every global market, you must participate in every global market. But think about the recent wreckage of the \"Metaverse\" trend or the SPAC boom. The investors who felt the compulsion to swing at those pitches—because they couldn't bear the FOMO—crushed their portfolios.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The modern superpower is looking at a wildly lucrative trend, admitting, \"I have absolutely no idea how this technology makes money,\" and going back to your things. You do not have to conquer every sector. You do not have to have an opinion on the Trade War's impact on microchips. Your edge is letting 99% of the modern noise fly right past you, saving your capital for the rare, boring, undeniable pitch that lands perfectly in your tiny zone of understanding.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Competence Interrogation\"",
                                description: "The next time you feel the urge to capitalize on a trending narrative (like a new AI infrastructure company), open your Chat tab. The Instruction: Force yourself to teach the business model to the AI. Type: \"Here is how [Company] generates free cash flow, and here is exactly why their competitors cannot steal their margins. Critique my thesis.\" If you cannot write that prompt clearly, or if the AI instantly demolishes your logic, you are swinging at a pitch outside your zone!",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 327,
                currentProgress: 0.0
            ),
            11: CoreChapterContent(
                chapterNumber: 11,
                chapterTitle: "Surviving the Coin Toss",
                bookTitle: "The Most Important Thing",
                bookAuthor: "Howard Marks",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Genius Delusion")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("There is a psychological trap in the financial world that quietly bankrupts the smartest people in the room: the asymmetry of ego. When we lose money, we immediately blame external forces—a sudden market crash, a corrupt CEO, or just bad luck. But when we make money, we look in the mirror and see a financial genius.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The friction here is that our brains are desperate to find cause and effect where there is only randomness. We fail at investing because we confuse a good outcome with a good process. You can make a brilliant, mathematically sound decision and still lose your shirt because of a freak geopolitical event. Conversely, you can make a brain-dead, reckless gamble and become a millionaire because you happened to be in the right place at the right time. The secret that Wall Street tries to bury is that a terrifying percentage of success is dictated by pure, unadulterated luck. If you cannot separate your skill from your luck, arrogance will eventually convince you to bet the house on a coin toss you are mathematically guaranteed to lose.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The National Coin-Flipping Tournament")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("To illustrate the absurdity of confusing luck with skill, imagine a national coin-flipping tournament.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Every single day, 330 million Americans wake up and flip a quarter. If you guess heads or tails correctly, you advance to the next round. If you guess wrong, you are eliminated. By the law of probability, after about 20 days, there will be roughly 315 people who have guessed correctly 20 times in a row.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Here is where the delusion kicks in. Those 315 people will genuinely believe they have a \"gift.\" They will start writing books titled The Art of the Toss. They will sell premium newsletter subscriptions detailing their proprietary \"wrist-flick algorithms\" and atmospheric pressure analyses. They will wear tailored suits and go on financial news networks to explain their genius. But the brutal math dictates that someone had to win. It wasn't skill; it was a statistical inevitability.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The lesson is to acknowledge \"alternative histories.\" For every wildly successful investor you see, there are a thousand invisible alternative realities where that exact same strategy wiped them out due to a single bad break. To survive the coin toss, you don't need a better wrist flick. You need a \"margin of safety\"—a structural defense in your portfolio that ensures when you inevitably guess wrong, you don't get permanently eliminated from the game.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Modern Context: The Algorithm's Blind Spot")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, the delusion of skill has been amplified by the sheer processing power we have at our fingertips.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("You can feed ten years of pristine market data into a deep learning architecture, carefully tuning an LSTM or training an XGBoost model to find the perfect predictive pattern. The backtesting looks flawless. The precision and recall metrics suggest you have solved the market. But here is the fatal flaw: models only train on the history that happened. They cannot train on the history that almost happened.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Your model cannot predict a sudden supply chain embargo on microchips, an overnight Trade War tariff, or a global pandemic that freezes the economy. When a highly leveraged bet on a Clean Energy ETF skyrockets, the investor praises their flawless algorithmic strategy. They completely ignore the alternative history where a single political vote swung the other way and bankrupted the sector. In modern finance, we build incredibly complex tools to predict the future, forgetting that the future is heavily dictated by random, unquantifiable variables. If your strategy relies on absolute precision rather than a massive margin of safety, you are just a coin flipper with a supercomputer.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Alternative History\" Stress Test",
                                description: "Before taking a heavy position in a company you are certain will succeed, open your Chat tab.",
                                isCompleted: false
                            ),
                            ActionStep(
                                title: "The Instruction",
                                description: "Prompt the AI: \"Give me three highly improbable 'black swan' events that would completely destroy the business model of [Ticker].\" Force yourself to look at the alternative histories where your \"sure thing\" goes to zero. If your portfolio cannot survive those scenarios, do more research!",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 274,
                currentProgress: 0.0
            ),
            12: CoreChapterContent(
                chapterNumber: 12,
                chapterTitle: "Winning by Not Losing",
                bookTitle: "The Most Important Thing",
                bookAuthor: "Howard Marks",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"Highlight Reel\" Trap")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("We are obsessed with the knockout punch. In every other arena of life, we are taught that greatness is the result of spectacular, aggressive action. We want to be the investor who spotted the next world-changing tech giant at two dollars a share, or the one who timed the exact bottom of a market crash. The friction here is that your ego wants a \"highlight reel,\" but your bank account needs a \"boring reality.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Most people fail at investing because they play a \"Winner's Game.\" They try to hit the impossible cross-court shot, betting their capital on high-risk, high-reward plays that require perfect timing and superhuman foresight. They focus so much on the potential \"win\" that they ignore the devastating math of the \"loss.\" In the markets, as in life, trying to be a hero usually just makes you a casualty. The insider secret is that wealth is not built by being right; it is built by refusing to be catastrophically wrong.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Amateur's Tennis Match")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author, John Bogle, explains this shift in mindset by borrowing a powerful observation from Simon Ramo’s book, Extraordinary Tennis for the Ordinary Player. Ramo noticed a fundamental difference between how professionals play tennis and how amateurs play it.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In professional tennis, it is a \"Winner's Game.\" Players are so skilled that they must hit powerful, precise \"winners\" to beat their opponents. But in amateur tennis, the game is entirely different. Amateurs don't win by hitting great shots; they win because their opponent eventually hits the ball into the net or out of bounds. It is a \"Loser's Game,\" where the person who makes the fewest unforced errors—the one who simply keeps the ball in play—is the one who walks away with the trophy.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Bogle argues that for 99% of us, the stock market is an amateur tennis match. You are not competing against a wall; you are competing against the costs of trading, the taxes of turnover, and the emotional volatility of your own brain. Every time you try to \"beat the market\" with a speculative bet, you are attempting a high-risk power shot. Most of the time, you hit it into the net. The author’s solution is to stop trying to hit winners. If you simply own the entire market through a low-cost index fund, you are effectively keeping the ball in play while everyone else exhausts themselves making unforced errors.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Modern Context: The High-Speed Net")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, the \"net\" is higher, and the game is played at a much faster pace. The challenge isn't the assets themselves—whether it's AI, Data Centers, or Crypto—it’s how we trade them.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The modern \"unforced error\" isn't buying a specific technology; it's the high-frequency tinkering that happens because of 24/7 access. When you look at high-volatility sectors like Crypto or AI-driven tech, the temptation is to \"play the swings.\" You see a 10% dip and try to time a perfect exit, or you see a vertical spike and try to leverage your position. These are the amateur's \"power shots.\" They feel like active management, but they often result in hitting the ball straight into the net of taxes, slippage, and bad timing.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In a world of \"0DTE\" options and instant liquidity, your greatest edge is your defensive posture. Whether you are holding a total market index or a specific digital asset, the person who wins is the one who refuses to be shaken out by short-term noise. Defensive investing today means realizing that the person who simply refuses to blow up their portfolio through over-trading or excessive fees is the one who stays on the court long enough to see the \"winners\" fail.")
                    ),
                ],
                audioDurationSeconds: 247,
                currentProgress: 0.0
            ),
            13: CoreChapterContent(
                chapterNumber: 13,
                chapterTitle: "Designing the Asymmetric Trade",
                bookTitle: "The Most Important Thing",
                bookAuthor: "Howard Marks",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Symmetrical Trap")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Most investors are trapped in a \"fair fight\" with the market, and in a fair fight, the house eventually wins. The friction here is the belief that to get higher returns, you must accept equally high risks. We are taught that if you want a 20% gain, you must be comfortable with a 20% loss. This is \"Symmetrical Investing,\" and it is a recipe for exhaustion.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("People fail because they treat their portfolio like a mirror—when the market goes up, they go up; when it crashes, they crash just as hard. They lack \"convexity.\" They are constantly one bad month away from wiping out a year of progress. The insider secret is that the elite don't play fair. They aren't looking for a 50/50 gamble; they are looking for \"Asymmetry.\" They want trades where the ceiling is the moon, but the floor is made of reinforced concrete. If you are participating in 100% of the market's pain just to get 100% of its gain, you aren't an investor; you’re a passenger.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Weighted Coin")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author, John Bogle, spent his career proving that the \"Alpha\" everyone chases—the ability to beat the market—is often just a ghost. But he highlights a specific, rare form of value-add: the ability to tilt the odds.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("He tells the story of the \"Gross Return\" vs. the \"Net Return.\" Imagine a game where you and the market both start with $100. The market goes up 10%, then down 10%. The market is back at $99. But if you have high fees, taxes, and bad timing, you might go up 8% and down 12%. You are at $95. You have \"Negative Asymmetry.\"")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Bogle’s solution was to use the \"Index\" as the ultimate defensive shield. He argued that by ruthlessly cutting costs and taxes, you create a natural asymmetry. While the active trader loses 2% of their wealth every year to the \"Croupiers\" (brokers and managers), the indexer keeps that 2%. Over decades, that 2% acts as a structural cushion. You participate in the full upside of the \"Real Market,\" but your \"Net\" downside is significantly less than the frantic gambler's. True skill isn't picking the winning horse; it’s making sure you don't pay the bookie so much that even a win makes you broke.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Modern Context: The \"Infrastructure\" Edge")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("In the modern landscape, asymmetry is found by shifting your focus from the \"front-end\" hype to the \"back-end\" necessity. Everyone is currently obsessed with picking the winning AI model or the fastest-growing clean energy startup. These are symmetric bets: if the specific technology fails or gets disrupted, the investment can vanish.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("True asymmetry today lies in the infrastructure that must exist regardless of which individual company wins the race. For instance, rather than gambling on a single AI software firm, the asymmetric move is looking at the power grid and data center real estate that the entire industry relies upon. If AI flourishes, these utilities are essential; if the hype cools, these assets still possess high intrinsic value and physical utility. This creates a \"heads I win, tails I don't lose much\" scenario.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Similarly, in sectors like Crypto or remote work tech, the goal is to avoid betting on the \"flash in the pan.\" Instead, you look for the protocols or service providers that have become the standard pipes of the system. By using your technical background—leveraging your understanding of system architecture and data mining—you can identify these \"bottleneck\" companies. You aren't guessing which app people will use; you are investing in the infrastructure they are forced to use. This is how you capture the massive upside of innovation while anchoring your floor in tangible, indispensable value.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Action Plan")
                    ),
                    CoreChapterSection(
                        type: .actionPlan,
                        title: nil,
                        content: .actionPlan([
                            ActionStep(
                                title: "The \"Infrastructure\" Check",
                                description: "Open your Chat tab and ask Caudex to identify the \"unsexy\" infrastructure providers for any hyped sector on your watchlist. Focus on companies that provide the power, land, or basic protocols required for that industry to function, ensuring your floor is supported by physical necessity rather than just sentiment.",
                                isCompleted: false
                            ),
                        ])
                    )
                ],
                audioDurationSeconds: 270,
                currentProgress: 0.0
            ),
            14: CoreChapterContent(
                chapterNumber: 14,
                chapterTitle: "The Final Checkmate",
                bookTitle: "The Most Important Thing",
                bookAuthor: "Howard Marks",
                sections: [
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The \"Just This Once\" Fallacy")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("You have survived the gauntlet. You understand the math of the market, the parasite of fees, and the illusion of the stock-picking guru. You have built a fortress. Yet, there is one final friction point where the smartest investors on earth still manage to destroy their own wealth: the finish line.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Most people fail here because they become victims of their own boredom. The brutal reality of compounding wealth is that it is extraordinarily monotonous. Once your system is set, there is nothing left to \"do.\" But human beings hate doing nothing. Eventually, your ego will whisper a dangerous lie: \"Just this once.\" You will see a friend double their net worth on a speculative gamble, or you will read a terrifying macroeconomic headline, and you will convince yourself that the rules you learned no longer apply to this one specific, unique situation. You will break your own system just to feel the thrill of participation. The insider secret is that wealth is not lost because an investor lacked knowledge; it is lost because they lacked the endurance to tolerate a lifetime of boredom. The final checkmate is protecting your portfolio from the greatest threat it will ever face: you.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Grand Master’s Blunder")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("The author, John Bogle, viewed the successful index investor as a chess Grand Master who has already mathematically won the game, but who still has to play out the final moves. The only way the Grand Master loses is if they get arrogant, lose focus, and make a careless, unforced blunder.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("To prevent this, Bogle created a final checklist of psychological pitfalls. He tells the story of the \"New Era\" delusion—the recurring historical fantasy where investors convince themselves that because technology has changed, the fundamental rules of corporate profits have somehow vanished. He warned against the \"Rearview Mirror\" trap, where investors blindly chase the sector that performed best last year, mathematically guaranteeing they pay peak prices.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Most importantly, Bogle preached the philosophy of \"Stay the Course.\" He argued that the ultimate investing superpower is an iron-clad commitment to your original plan, regardless of market weather. The successful investor does not pivot when the market crashes, nor do they leverage up when the market soars. They simply pull all the principles together—low costs, broad diversification, and zero emotion—and they lock the door. The game is won by the player who refuses to make the final, fatal blunder.")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("The Modern Context: The Algorithmic Siren Song")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Today, the \"Just this once\" fallacy is no longer a quiet whisper; it is a deafening roar amplified by algorithms.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Every time you open your phone, the financial machine is trying to force you into a blunder. It presents you with \"New Era\" narratives that feel impossible to ignore. A breakthrough in Generative AI, a sudden regulatory shift in Crypto, or a geopolitical Trade War—each event is packaged by financial media as an unprecedented emergency requiring your immediate action.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("You will see new \"Thematic ETFs\" launched precisely at the peak of a trend’s hype, charging premium fees for the privilege of owning what is already popular. The modern checkmate requires you to view this entire digital ecosystem not as a source of actionable intelligence, but as a psychological testing ground. When everyone on your timeline is screaming that a specific Data Center stock or Clean Energy token is \"going to the moon,\" your discipline must be a brick wall. The ultimate modern edge is looking at the most exciting, paradigm-shifting technological revolution in human history, and having the sheer audacity to say, \"That is fascinating, but I am not touching my portfolio.\" ***")
                    ),
                    CoreChapterSection(
                        type: .heading,
                        title: nil,
                        content: .text("Final Words: The Ultimate Edge")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("As we close this journey through the principles of common sense investing, remember what you actually possess. You are not just using an app; you are wielding the mathematical truth of the markets. The financial industry will always try to sell you complexity, because complexity is profitable for them. But simplicity is profitable for you.")
                    ),
                    CoreChapterSection(
                        type: .paragraph,
                        title: nil,
                        content: .text("Your journey through these 14 Cores was designed to strip away the noise. You now know that the \"croupiers\" in suits and the \"influencers\" on screens are playing a different game than you are. Let them chase the wind. Let them pay the taxes, the fees, and the emotional toll of the casino. Your money is quietly, ruthlessly capturing the growth of human progress. Trust the math, ignore the crowd, and above all else, stay the course.")
                    ),
                ],
                audioDurationSeconds: 299,
                currentProgress: 0.0
            ),
        ],
    ]
}

extension BookCoreChapter {
    /// The real Core list (timeline rows) per book, keyed by curriculumOrder.
    static let listsByOrder: [Int: [BookCoreChapter]] = [
        1: [
            BookCoreChapter(number: 1, title: "De-Programming the \"Employee\" Mindset", description: "You wake up, you go to work, you pay your bills, you wait for the weekend. Repeat until you die. This isn't just a routine; it’s a…"),
            BookCoreChapter(number: 2, title: "Mastering the Financial Scorecard", description: "Here is the secret that keeps the middle class exhausted: You are looking at the wrong scoreboard. Most professionals believe that…"),
            BookCoreChapter(number: 3, title: "The Corporate Shield Strategy", description: "Here is the raw deal you signed up for without reading the fine print: You work for money, the government takes a huge bite (taxes…"),
            BookCoreChapter(number: 4, title: "The Opportunity Hunter", description: "Most people are blind. They walk down the street and see buildings, businesses, and people. A wealthy person walks down the same s…"),
            BookCoreChapter(number: 5, title: "Trading Security for Skills", description: "Here is the lie that universities sell you: \"Specialize to succeed.\" They tell you to become the best neurosurgeon, the sharpest c…"),
            BookCoreChapter(number: 6, title: "Conquering the Inner Saboteur", description: "Here is the tragedy: You have the financial literacy. You understand assets vs. liabilities. You see the deal. And then… you freez…"),
            BookCoreChapter(number: 7, title: "The \"First 3 Steps\" Launchpad", description: "Here is the final, most dangerous trap: You are smarter now. You have read the Cores. You understand the math. And yet, tomorrow m…"),
        ],
        2: [
            BookCoreChapter(number: 1, title: "Drawing the Battle Lines", description: "Here is the uncomfortable truth: 90% of people who think they are \"investing\" are actually just gambling with better odds. The fri…"),
            BookCoreChapter(number: 2, title: "The Invisible Enemy", description: "The greatest trick the financial industry ever pulled was convincing you that cash is \"safe.\" You look at your bank account, and t…"),
            BookCoreChapter(number: 3, title: "Mastering the Manic-Depressive Market", description: "Imagine you own a house you love. You know its value: good roof, solid foundation, great neighborhood. Now imagine a stranger stan…"),
            BookCoreChapter(number: 4, title: "Building the Fortress (The Defensive Strategy)", description: "There is a persistent lie in finance that \"effort equals return.\" In your job, if you work 80 hours a week, you get a promotion. I…"),
            BookCoreChapter(number: 5, title: "Here is the content for Core 5, where we outline the strict rules for those who want to beat the market.", description: "Here is the seduction: You believe that if you are smarter, faster, and read more news than your neighbor, you will make more mone…"),
            BookCoreChapter(number: 6, title: "The Mutual Fund Maze", description: "The financial industry is built on a single, powerful myth: \"Investing is too complicated for you to do alone; you need an expert.…"),
            BookCoreChapter(number: 7, title: "The Earnings Mirage", description: "The greatest lie on Wall Street is a single number: \"EPS\" (Earnings Per Share). The friction is that you are trained to treat this…"),
            BookCoreChapter(number: 8, title: "The Comparison Test", description: "The biggest mistake you make is falling in love with a stock in isolation. You see a company—let's say a popular coffee chain—and…"),
            BookCoreChapter(number: 9, title: "Filtering for Quality (The Defensive Screen)", description: "Most investors suffer from financial obesity. You fill your portfolio with \"empty calories\"—companies that have great stories but…"),
            BookCoreChapter(number: 10, title: "Hunting for Bargains (The Enterprising Screen)", description: "To be an Enterprising Investor, you must be willing to do something that feels physically repulsive: you must buy what others are…"),
            BookCoreChapter(number: 11, title: "Spotting the Red Flags", description: "The most dangerous defect in the human brain is the ability to ignore data that contradicts a happy story. You buy a stock because…"),
            BookCoreChapter(number: 12, title: "The Golden Rule (Margin of Safety)", description: "The biggest lie you tell yourself is that you can predict the future. You build complex spreadsheets projecting revenue for the ne…"),
        ],
        3: [
            BookCoreChapter(number: 1, title: "The Spreadsheet Delusion", description: "Have you ever noticed how the smartest people in the room often make the most disastrous financial decisions? The friction comes f…"),
            BookCoreChapter(number: 2, title: "The Illusion of Complete Control", description: "Listen closely. The financial world is obsessed with the myth of the self-made visionary. When we see someone accumulate massive w…"),
            BookCoreChapter(number: 3, title: "The Moving Goalpost Syndrome", description: "Have you ever noticed that hitting your financial targets rarely feels as good as you thought it would? You hustle, you scrape, yo…"),
            BookCoreChapter(number: 4, title: "Ignite the Compounding Engine", description: "Here is a secret that the loudest voices in the financial media desperately want you to ignore: hunting for the highest possible r…"),
            BookCoreChapter(number: 5, title: "The Schizophrenia of Wealth", description: "Getting wealthy is glamorous; staying wealthy is agonizingly dull. The financial world glorifies the ascent. We obsess over the vi…"),
            BookCoreChapter(number: 6, title: "The Invisible Chain", description: "Here is a reality most people completely misunderstand about capital: we are conditioned to view money strictly as a medium of exc…"),
            BookCoreChapter(number: 7, title: "The Illusion of the Visible", description: "Here is a psychological trap that almost everyone falls into: we rely on visual cues to measure success. If you want to know if so…"),
            BookCoreChapter(number: 8, title: "The Map is Not the Territory", description: "Here is the most dangerous assumption embedded in modern finance: the belief that the past is a perfect blueprint for the future.…"),
            BookCoreChapter(number: 9, title: "The Mirror Trap", description: "Here is the invisible trapdoor in the financial world: assuming everyone on the field is playing the exact same sport. Most people…"),
            BookCoreChapter(number: 10, title: "The Intellectual Allure of Doom", description: "Sit in any busy coffee shop, open up your laptop, and scroll through the day's financial headlines. Notice how the articles predic…"),
            BookCoreChapter(number: 11, title: "The Mirage of Certainty", description: "Look at the current state of the global market. We are navigating an era of unprecedented chaos—shifting interest rates, volatile…"),
            BookCoreChapter(number: 12, title: "The Myth of the Master Key", description: "Walk into any bookstore or log onto any financial forum, and you will immediately notice a common delusion: everyone is desperatel…"),
        ],
        4: [
            BookCoreChapter(number: 1, title: "Flipping the Script on Wall Street", description: "Come closer. Let’s be honest about why you’re nervous. You think the game is rigged. You look at the guys in the glass towers with…"),
            BookCoreChapter(number: 2, title: "The Mirror Test & Risk Tolerance", description: "Most people treat the stock market like a casino, hoping for a lucky spin, but panicking the moment the dealer takes a chip. They…"),
            BookCoreChapter(number: 3, title: "Ignoring the Macro Noise", description: "The single biggest waste of time for any investor is trying to predict the economy. You sit there, paralyzed, watching CNBC, terri…"),
            BookCoreChapter(number: 4, title: "Leveraging Your Daily Routine", description: "You probably think the best stock tips are hidden in a locked room on Wall Street, guarded by men in $5,000 suits whispering about…"),
            BookCoreChapter(number: 5, title: "The \"Perfect Stock\" Profile", description: "You are addicted to \"sexy.\" You want to own the company that’s curing cancer, mining asteroids, or inventing the next iPhone. You…"),
            BookCoreChapter(number: 6, title: "The Six Categories of Opportunity", description: "You are judging a fish by its ability to climb a tree. You buy a utility company and get angry when it doesn't double in six month…"),
            BookCoreChapter(number: 7, title: "The Earnings Engine", description: "You are hypnotized by the wrong number. You wake up, check your phone, and see a stock price. It’s up $5. You feel brilliant. It’s…"),
            BookCoreChapter(number: 8, title: "Financial Forensics", description: "You are driving a sports car at 150 miles per hour, but you’ve taped over the fuel gauge because \"math is boring.\" You love the sp…"),
            BookCoreChapter(number: 9, title: "Assessing Management & Dividends", description: "You love a charismatic CEO. You watch them on TV, radiating confidence, talking about \"synergies\" and \"ecosystems,\" and you think,…"),
            BookCoreChapter(number: 10, title: "Designing the Allocation", description: "You are likely guilty of the most destructive habit in investing: \"Cutting the flowers and watering the weeds.\" You buy a stock, a…"),
            BookCoreChapter(number: 11, title: "The Re-Evaluation Loop", description: "You treat your stocks like a marriage, sticking with them \"for better or worse\" long after the love is gone. You bought a company…"),
            BookCoreChapter(number: 12, title: "The Exit Protocols", description: "Buying a stock is like falling in love—it’s exciting, full of promise, and driven by dopamine. Selling a stock is like a divorce—i…"),
        ],
        5: [
            BookCoreChapter(number: 1, title: "The 'Scuttlebutt' Investigation", description: "Here is the uncomfortable truth: if you are making investment decisions based solely on annual reports or CNBC headlines, you are…"),
            BookCoreChapter(number: 2, title: "Auditing the Engine (Sales & Innovation)", description: "Here is the most expensive mistake you will ever make: assuming the best product wins. You find a tech company with faster chips,…"),
            BookCoreChapter(number: 3, title: "Decoding Management DNA", description: "You watch the keynote. The CEO strides across the stage in a leather jacket, promising to revolutionize the industry with a single…"),
            BookCoreChapter(number: 4, title: "Sniper Entries & The Myth of Market Timing", description: "Here is how you lose a fortune while trying to save lunch money. You find the perfect company. You’ve done the \"Scuttlebutt.\" You…"),
            BookCoreChapter(number: 5, title: "The Art of the 'Forever Hold'", description: "This is the most painful lesson you will ever learn: you will likely make more money from the one stock you didn't sell than from…"),
            BookCoreChapter(number: 6, title: "Immunizing Against Noise", description: "Here is why you panic-sold at the bottom: You listened to a \"Macro Tourist.\" You let a stranger on television, who has never analy…"),
        ],
        6: [
            BookCoreChapter(number: 1, title: "Escaping the Intermediary Trap", description: "Listen closely. The reason most intelligent people fail at building wealth isn’t that they pick the wrong stocks. It’s that they a…"),
            BookCoreChapter(number: 2, title: "Separating Business from Speculation", description: "There is a dangerous optical illusion that blinds almost everyone who opens a brokerage app. You see a green line going up, and yo…"),
            BookCoreChapter(number: 3, title: "Accepting the Zero-Sum Game", description: "Here is the hardest pill to swallow. You have been told that if you study hard enough, read enough charts, and listen to enough po…"),
            BookCoreChapter(number: 4, title: "Defeating the Tyranny of Compounding Costs", description: "There is a thief in your portfolio, and he is invisible. You spend hours researching whether \"Clean Energy\" or \"AI\" will boom next…"),
            BookCoreChapter(number: 5, title: "Plugging the Tax Leak", description: "There is a silent partner in your portfolio who contributes zero capital, takes zero risk, yet claims up to 37% of your profits. Y…"),
            BookCoreChapter(number: 6, title: "Ignoring the Siren Song of \"Stars\"", description: "There is a fatal flaw in human psychology that the financial industry exploits with ruthless efficiency: We believe that what just…"),
            BookCoreChapter(number: 7, title: "Buying the Haystack", description: "We are all born with a fatal flaw in our financial DNA: the belief that we are smarter than the average. The financial industry kn…"),
            BookCoreChapter(number: 8, title: "Respecting the Law of Gravity", description: "There is a seduction in the stock market that destroys more wealth than any recession: the Parabola. You see a stock chart that lo…"),
            BookCoreChapter(number: 9, title: "Anchoring with Bonds", description: "There is a dangerous allergy in the modern investor's mind: an allergy to \"boring.\" You look at your portfolio and think, \"Why wou…"),
            BookCoreChapter(number: 10, title: "Navigating the ETF Minefield", description: "There is a paradox in modern finance: You have never had more access, yet you have never been more likely to blow yourself up. In…"),
            BookCoreChapter(number: 11, title: "Avoiding the \"Smart Beta\" Trap", description: "There is a term in finance invented purely to make you feel inadequate: \"Smart Beta.\" It implies, rather rudely, that the standard…"),
            BookCoreChapter(number: 12, title: "Auditing Your Advisor", description: "We often hire financial advisors because we are scared. The stock market feels like a jungle, and we want a guide with a machete t…"),
            BookCoreChapter(number: 13, title: "Mastering the Art of Doing Nothing", description: "There is a fatal flaw in human evolution that makes you a terrible investor: the biological bias for action. For two hundred thous…"),
        ],
        7: [
            BookCoreChapter(number: 1, title: "The Valuation Duel", description: "There is a secret war happening on Wall Street, and you are the casualty. Most investors think the stock market is one big casino…"),
            BookCoreChapter(number: 2, title: "Spotting Financial Hallucinations", description: "Here is the embarrassing truth: High IQ does not protect you from financial stupidity. In fact, smart people are often more suscep…"),
            BookCoreChapter(number: 3, title: "The Technical Analysis Trap", description: "Humans are desperate for patterns. If you stare at clouds long enough, you see faces. If you stare at stock charts long enough, yo…"),
            BookCoreChapter(number: 4, title: "The Fundamental Illusion", description: "Here is the most painful lesson for smart people: You can do everything right and still lose. You can read every balance sheet, ca…"),
            BookCoreChapter(number: 5, title: "Respecting the Efficiency Engine", description: "Here is the hardest pill to swallow: You are not smarter than the market. Nobody is. The friction is your ego. You believe that be…"),
            BookCoreChapter(number: 6, title: "The Art of Risk Engineering", description: "Most investors are walking around with a ticking time bomb in their pockets, and they call it a \"diversified portfolio.\" You think…"),
            BookCoreChapter(number: 7, title: "Decoding \"Smart\" Strategies", description: "Wall Street has a massive problem: Simple index funds work too well. They are cheap, effective, and boring. This is a disaster for…"),
            BookCoreChapter(number: 8, title: "Conquering Your Inner Ape", description: "Here is the biological glitch that destroys portfolios: Your brain is 200,000 years old. It was designed to survive on the savanna…"),
            BookCoreChapter(number: 9, title: "The Indexing Manifesto", description: "Here is the scandal of the century: The financial industry is the only industry on Earth where you get worse service the more you…"),
            BookCoreChapter(number: 10, title: "The Lifecycle Wealth Map", description: "Here is the most expensive mistake smart people make: They treat investing like a static number. They ask, \"What is the best portf…"),
        ],
        8: [
            BookCoreChapter(number: 1, title: "The Ticker Tape Illusion", description: "Here is the friction: most investors treat the stock market like a casino and their shares like betting chips. They obsess over th…"),
            BookCoreChapter(number: 2, title: "Decoding the True Economics", description: "The Profit Mirage Here is the dangerous secret about financial statements: \"Profit\" is an opinion, but \"Cash\" is a fact. Most aspi…"),
            BookCoreChapter(number: 3, title: "Determining the Strength of the Moat", description: "The \"Good Product\" Fallacy Here is the friction: most investors think a \"good company\" is one with a great product or a fast-growi…"),
            BookCoreChapter(number: 4, title: "Mastering Market Psychology", description: "You believe the market is an authority figure. When a stock price collapses by 10% in a single morning, your primal brain assumes…"),
            BookCoreChapter(number: 5, title: "The Art of Capital Deployment", description: "The CEO’s Secret Addiction Here is the friction: You assume the CEO works for you. You assume their primary goal is to make your s…"),
            BookCoreChapter(number: 6, title: "Escaping the Institutional Trap", description: "The Boardroom Blind Spot We treat CEOs like chess grandmasters. We assume that when a leader announces a massive acquisition or a…"),
        ],
        9: [
            BookCoreChapter(number: 1, title: "The Grand Illusion of Wall Street", description: "Listen closely. We are navigating an era of unprecedented financial chaos. Between inflation silently eating away your purchasing…"),
            BookCoreChapter(number: 2, title: "The Myth of the Rational Machine", description: "In the realms of computer science and finance, we are trained to search for elegant algorithms and rational models to predict beha…"),
            BookCoreChapter(number: 3, title: "The Casino Chip Delusion", description: "Let me pull back the curtain on the greatest psychological trap in modern finance. In today’s hyper-connected, digital world, the…"),
            BookCoreChapter(number: 4, title: "The Steamroller and the Pennies", description: "Most retail investors fail because they step onto a battlefield where they are mathematically guaranteed to lose. When two compani…"),
            BookCoreChapter(number: 5, title: "The Spreadsheet Delusion", description: "Have you ever noticed how the smartest people in the room often make the most disastrous financial decisions? The friction comes f…"),
        ],
        10: [
            BookCoreChapter(number: 1, title: "The Illusion of the Crowded Room", description: "Here is the uncomfortable truth most retail investors never figure out: the market is a giant, ruthlessly efficient weighing machi…"),
            BookCoreChapter(number: 2, title: "The Perfect Machine Fallacy", description: "Most people walk into the financial markets believing a very dangerous academic lie: that the market is a perfectly calibrated mac…"),
            BookCoreChapter(number: 3, title: "The Fatal Halo Effect", description: "Most people conflate a great company with a great investment. It is the most expensive mistake in finance. You see a business with…"),
            BookCoreChapter(number: 4, title: "The Volatility Illusion", description: "The financial industry has brainwashed you with a mathematical lie. They desperately want you to believe that \"risk\" simply means…"),
            BookCoreChapter(number: 5, title: "The Illusion of the Risk-Reward Contract", description: "Here is the silent trap most investors fall into: they treat risk like a binding contract. They look at a highly speculative asset…"),
            BookCoreChapter(number: 6, title: "The Crystal Ball Delusion", description: "Humans are desperately uncomfortable with uncertainty, which is why the financial industry makes billions selling the illusion of…"),
            BookCoreChapter(number: 7, title: "Combating Emotional Gravity", description: "The greatest threat to your financial survival is not the Federal Reserve, a market crash, or a geopolitical crisis. It is your ow…"),
            BookCoreChapter(number: 8, title: "The Illusion of Safety in Numbers", description: "There is a paradox in investing that breaks the brains of most beginners: The safer an investment feels, the more dangerous it act…"),
            BookCoreChapter(number: 9, title: "Scavenging for Neglected Value", description: "There is a dangerous assumption that a \"world-changing company\" automatically makes for a \"great investment.\" The friction here is…"),
            BookCoreChapter(number: 10, title: "The Discipline of Inaction", description: "There is a toxic lie baked into our culture that dictates how we measure success: we worship the \"hustle.\" From the moment you ent…"),
            BookCoreChapter(number: 11, title: "Surviving the Coin Toss", description: "There is a psychological trap in the financial world that quietly bankrupts the smartest people in the room: the asymmetry of ego.…"),
            BookCoreChapter(number: 12, title: "Winning by Not Losing", description: "We are obsessed with the knockout punch. In every other arena of life, we are taught that greatness is the result of spectacular,…"),
            BookCoreChapter(number: 13, title: "Designing the Asymmetric Trade", description: "Most investors are trapped in a \"fair fight\" with the market, and in a fair fight, the house eventually wins. The friction here is…"),
            BookCoreChapter(number: 14, title: "The Final Checkmate", description: "You have survived the gauntlet. You understand the math of the market, the parasite of fees, and the illusion of the stock-picking…"),
        ],
    ]
}

extension LibraryBook {
    /// Total read time (minutes) per book, computed from the authored core content.
    static let readMinutesByOrder: [Int: Int] = [
        1: 22,  // Rich Dad Poor Dad
        2: 38,  // The Intelligent Investor
        3: 56,  // The Psychology of Money
        4: 39,  // One Up On Wall Street
        5: 19,  // Common Stocks and Uncommon Profits
        6: 48,  // The Little Book of Common Sense Investing
        7: 32,  // A Random Walk Down Wall Street
        8: 19,  // The Essays of Warren Buffett
        9: 24,  // The Little Book that Still Beats the Market
        10: 58,  // The Most Important Thing
    ]
}
