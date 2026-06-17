//
//  BookReadAlong.swift
//  ios
//
//  Per-sentence read-along timings for the Book Library narration. For each book/core, the list of
//  narrated blocks (headings + paragraphs, in render order; the action plan is excluded) with each
//  sentence's start/end offset (seconds) within the single book audio file. Drives sentence
//  highlighting in BookCoreDetailView as the narration plays.
//
//  Generated from backend/data/book_audio/*.manifest.json + the authored core text by
//  backend/scripts/gen_book_read_along.py. Do not hand-edit — regenerate from source.
//

import Foundation

struct ReadAlongSentence {
    let text: String
    let start: Double
    let end: Double
}

struct ReadAlongBlock {
    let isHeading: Bool
    let sentences: [ReadAlongSentence]
}

extension ReadAlongBlock {
    /// [curriculumOrder: [coreNumber: [blocks in narration order]]].
    static let byBook: [Int: [Int: [ReadAlongBlock]]] = [
        1: [
            1: [
                ReadAlongBlock(isHeading: true, sentences: [
                    ReadAlongSentence(text: "The Hamster Wheel of Emotion", start: 16.02, end: 17.64),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "You wake up, you go to work, you pay your bills, you wait for the weekend.", start: 18.58, end: 22.52),
                    ReadAlongSentence(text: "Repeat until you die.", start: 22.98, end: 24.42),
                    ReadAlongSentence(text: "This isn't just a routine; it’s a trap designed by your own biology.", start: 25.36, end: 29.42),
                    ReadAlongSentence(text: "Most people are driven by two primal emotions: Fear (of not having money) and Greed (the desire for a better lifestyle).", start: 30.18, end: 37.38),
                    ReadAlongSentence(text: "When you get a raise, the Fear temporarily subsides, but Greed immediately kicks in, whispering that you need a new car, a bigger house, or that luxury vacation.", start: 38.26, end: 47.87),
                    ReadAlongSentence(text: "So, you spend the raise.", start: 48.79, end: 49.97),
                    ReadAlongSentence(text: "The Fear returns.", start: 50.41, end: 51.17),
                    ReadAlongSentence(text: "You run faster.", start: 51.59, end: 52.57),
                    ReadAlongSentence(text: "You are in the Rat Race, and the friction burning you out isn't your boss—it’s your own emotional reaction to money.", start: 53.11, end: 59.37),
                ]),
                ReadAlongBlock(isHeading: true, sentences: [
                    ReadAlongSentence(text: "The 10-Cent Lesson", start: 60.69, end: 61.79),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "The author tells the story of being nine years old and making a deal with his \"Rich Dad.", start: 62.85, end: 66.77),
                    ReadAlongSentence(text: "He agrees to work in a convenience store for 10 cents an hour.", start: 67.41, end: 70.23),
                    ReadAlongSentence(text: "He slaves away for three weeks, dusting cans, hating the work, and feeling exploited.", start: 70.57, end: 75.79),
                    ReadAlongSentence(text: "Finally, he marches to his Rich Dad to quit, demanding a raise.", start: 76.43, end: 79.91),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "Rich Dad smiles.", start: 80.93, end: 81.85),
                    ReadAlongSentence(text: "\"Now you sound like most of my employees,\" he says.", start: 82.35, end: 84.99),
                    ReadAlongSentence(text: "He explains that if he had just given the boy a raise, he would have learned nothing but how to be a better employee.", start: 85.85, end: 90.71),
                    ReadAlongSentence(text: "Instead, Rich Dad offers him a new deal: \"I’ll teach you, but I won’t pay you at all.", start: 91.17, end: 95.73),
                    ReadAlongSentence(text: "By taking the paycheck away, the boy was forced to stop thinking like a worker looking for a wage and start thinking like an owner looking for an opportunity.", start: 96.57, end: 103.77),
                    ReadAlongSentence(text: "His brain, no longer sedated by a salary, eventually spotted a business opportunity right in the store—a comic book library—that made him far more than 10 cents.", start: 104.29, end: 113.63),
                ]),
                ReadAlongBlock(isHeading: true, sentences: [
                    ReadAlongSentence(text: "The 2026 Trap", start: 114.99, end: 116.21),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "In 2026, the Rat Race has upgraded.", start: 116.75, end: 119.86),
                    ReadAlongSentence(text: "It’s no longer just about paying bills; it’s about the \"Golden Handcuffs\" of remote work comfort and the subtle threat of AI displacement.", start: 120.32, end: 128.1),
                    ReadAlongSentence(text: "The \"safe job\" is a myth.", start: 128.62, end: 130.42),
                    ReadAlongSentence(text: "If your primary income relies on trading hours for dollars, you are essentially shorting your own future.", start: 130.96, end: 136.54),
                    ReadAlongSentence(text: "The modern employee mindset says, \"I need to learn prompt engineering so I don't get fired.", start: 137.18, end: 141.78),
                    ReadAlongSentence(text: "The wealthy mindset says, \"I will build an AI agent that does the work for me while I sleep.", start: 142.26, end: 147.2),
                    ReadAlongSentence(text: "The trap today is thinking that \"upskilling\" for a salary is freedom.", start: 147.96, end: 151.42),
                    ReadAlongSentence(text: "It is not.", start: 151.84, end: 152.4),
                    ReadAlongSentence(text: "It is just a shinier wheel.", start: 152.7, end: 154.2),
                ]),
            ],
            2: [
                ReadAlongBlock(isHeading: true, sentences: [
                    ReadAlongSentence(text: "The Illusion of Wealth", start: 172.18, end: 173.42),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "Here is the secret that keeps the middle class exhausted: You are looking at the wrong scoreboard.", start: 174.5, end: 179.1),
                    ReadAlongSentence(text: "Most professionals believe that a high salary equals wealth.", start: 179.62, end: 182.3),
                    ReadAlongSentence(text: "It doesn’t.", start: 182.74, end: 183.2),
                    ReadAlongSentence(text: "You can earn $250,000 a year and still be technically insolvent if your monthly burn rate matches your income.", start: 183.72, end: 190.26),
                    ReadAlongSentence(text: "The friction isn't your paycheck; it’s your financial literacy.", start: 190.76, end: 193.64),
                    ReadAlongSentence(text: "You have been trained to read words, but you haven't been trained to read numbers.", start: 194.34, end: 197.66),
                    ReadAlongSentence(text: "Consequently, you spend your life building someone else's business (your boss's), buying someone else's investments (the bank's), and paying someone else's bills (the government's).", start: 198.1, end: 207.26),
                ]),
                ReadAlongBlock(isHeading: true, sentences: [
                    ReadAlongSentence(text: "The Tale of Two Columns", start: 208.32, end: 209.64),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "The author illustrates this with a simple, brutal truth that shattered the worldview of a young couple.", start: 210.72, end: 215.62),
                    ReadAlongSentence(text: "They celebrate a pay raise by buying their \"dream home.", start: 216.16, end: 218.72),
                    ReadAlongSentence(text: "They proudly list this house under the \"Asset\" column of their financial statement.", start: 219.18, end: 222.62),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "But the author's mentor, the \"Rich Dad,\" draws a simple diagram to prove them wrong.", start: 223.32, end: 227.46),
                    ReadAlongSentence(text: "He defines an asset not by accounting tradition, but by the direction of cash flow.", start: 227.9, end: 231.88),
                    ReadAlongSentence(text: "An asset puts money in your pocket.", start: 232.38, end: 234.11),
                    ReadAlongSentence(text: "A liability takes money out.", start: 234.47, end: 236.15),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "Because that house requires a mortgage, property taxes, insurance, and maintenance, cash is flowing out.", start: 236.61, end: 241.63),
                    ReadAlongSentence(text: "Therefore, the house is a liability.", start: 241.99, end: 243.79),
                    ReadAlongSentence(text: "The \"Rich Dad\" explains that the poor work for money to pay expenses; the middle class buys liabilities they think are assets (like houses and cars); but the rich focus entirely on the Asset Column—acquiring things that generate cash while they sleep.", start: 244.43, end: 256.51),
                ]),
                ReadAlongBlock(isHeading: true, sentences: [
                    ReadAlongSentence(text: "The 2026 Camouflage", start: 257.49, end: 259.11),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "In 2026, the \"Liability Trap\" is even more sophisticated.", start: 259.57, end: 263.97),
                    ReadAlongSentence(text: "It’s no longer just a house; it’s digital.", start: 264.57, end: 266.83),
                    ReadAlongSentence(text: "It is the crypto \"investment\" that has zero utility and generates no yield—that is speculation, not an asset.", start: 267.53, end: 273.97),
                    ReadAlongSentence(text: "It is the five different AI software subscriptions you pay for monthly but don’t use to generate income.", start: 274.71, end: 279.81),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "In the modern era, true assets have evolved.", start: 280.77, end: 283.19),
                    ReadAlongSentence(text: "An asset might be a high-yield DeFi staking protocol, a dividend-paying ETF, or a piece of code you wrote once that sells itself repeatedly.", start: 283.83, end: 291.77),
                    ReadAlongSentence(text: "Conversely, \"Buy Now, Pay Later\" schemes for consumer goods are the modern shackles.", start: 292.65, end: 297.11),
                    ReadAlongSentence(text: "The principle remains: If you have to work to keep it, it's a job.", start: 297.83, end: 301.47),
                    ReadAlongSentence(text: "If you have to pay to keep it, it's a liability.", start: 302.05, end: 304.25),
                    ReadAlongSentence(text: "If it pays you to keep it, it's an asset.", start: 304.91, end: 307.01),
                ]),
            ],
            3: [
                ReadAlongBlock(isHeading: true, sentences: [
                    ReadAlongSentence(text: "The Government's First Bite", start: 323.71, end: 325.01),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "Here is the raw deal you signed up for without reading the fine print: You work for money, the government takes a huge bite (taxes), and you try to live on what’s left.", start: 326.05, end: 335.43),
                    ReadAlongSentence(text: "The harder you work, the more they take.", start: 335.97, end: 337.83),
                    ReadAlongSentence(text: "It’s a rigged game.", start: 338.43, end: 339.33),
                    ReadAlongSentence(text: "Most people think tax law is a punishment for making money.", start: 340.05, end: 342.57),
                    ReadAlongSentence(text: "It isn’t.", start: 343.17, end: 343.55),
                    ReadAlongSentence(text: "It’s an incentive system designed by the wealthy to reward the wealthy.", start: 344.13, end: 347.35),
                    ReadAlongSentence(text: "If you are an employee, you are playing the game on \"Hard Mode.", start: 348.13, end: 351.05),
                    ReadAlongSentence(text: "You earn, you get taxed, and then you spend.", start: 351.49, end: 354.11),
                    ReadAlongSentence(text: "You are paying the bill for everyone else's roads and schools before you even buy your own groceries.", start: 354.81, end: 359.41),
                ]),
                ReadAlongBlock(isHeading: true, sentences: [
                    ReadAlongSentence(text: "The Magic Folder", start: 361.93, end: 362.79),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "The author reveals a secret that completely demystifies the word \"Corporation.", start: 363.75, end: 367.53),
                    ReadAlongSentence(text: "He explains that a corporation isn't a factory with smokestacks or a skyscraper with a logo.", start: 368.17, end: 372.89),
                    ReadAlongSentence(text: "It is simply a file folder with some legal documents in it.", start: 373.47, end: 376.33),
                    ReadAlongSentence(text: "It is a legal entity that creates a body without a soul.", start: 376.89, end: 379.61),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "Why does this matter?", start: 380.69, end: 381.45),
                    ReadAlongSentence(text: "Because this \"body\" has magic powers.", start: 382.01, end: 384.22),
                    ReadAlongSentence(text: "The rich use this legal shield to flip the script on the government.", start: 384.84, end: 387.76),
                    ReadAlongSentence(text: "An individual earns, pays tax, and spends what is left.", start: 388.32, end: 391.72),
                    ReadAlongSentence(text: "A corporation earns, spends everything it can (expenses), and then pays tax on what is left.", start: 392.34, end: 397.9),
                    ReadAlongSentence(text: "The author tells the story of how he uses his corporation to pay for his car, his \"board meetings\" in Hawaii, and his health club membership—all with pre-tax dollars.", start: 398.56, end: 407.14),
                    ReadAlongSentence(text: "The corporation is the shield that protects his wealth from the taxman’s first bite.", start: 407.7, end: 411.66),
                ]),
                ReadAlongBlock(isHeading: true, sentences: [
                    ReadAlongSentence(text: "The Solopreneur's Edge", start: 414.06, end: 415.3),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "In 2026, you don't need a boardroom to have a corporation.", start: 415.92, end: 419.64),
                    ReadAlongSentence(text: "You just need a laptop.", start: 420.12, end: 421.06),
                    ReadAlongSentence(text: "The \"Gig Economy\" and remote work have democratized the Corporate Shield.", start: 421.78, end: 425.4),
                    ReadAlongSentence(text: "If you are a freelancer, a content creator, or a consultant, you are a business.", start: 426.16, end: 430.78),
                    ReadAlongSentence(text: "That high-end laptop you use for coding?", start: 431.7, end: 433.6),
                    ReadAlongSentence(text: "That’s a business expense.", start: 434.04, end: 435.12),
                    ReadAlongSentence(text: "That trip to a tech conference in Tokyo?", start: 435.82, end: 437.5),
                    ReadAlongSentence(text: "That’s travel and education.", start: 438.02, end: 439.28),
                    ReadAlongSentence(text: "Even a portion of your home internet and rent can be a write-off if you have a compliant home office.", start: 440.18, end: 444.88),
                    ReadAlongSentence(text: "The modern wealthy don't evade taxes; they just use the incentives (like depreciation on real estate or R&D credits for AI development) that the government wants them to use.", start: 445.68, end: 455.8),
                ]),
            ],
            4: [
                ReadAlongBlock(isHeading: true, sentences: [
                    ReadAlongSentence(text: "The \"I Can't\" Paralysis", start: 471.78, end: 473.36),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "Most people are blind.", start: 474.68, end: 475.74),
                    ReadAlongSentence(text: "They walk down the street and see buildings, businesses, and people.", start: 476.04, end: 479.3),
                    ReadAlongSentence(text: "A wealthy person walks down the same street and sees deals.", start: 479.9, end: 483.12),
                    ReadAlongSentence(text: "The friction for the average person is a mental firewall: the belief that \"I don't have the money\" is a stop sign.", start: 483.8, end: 489.64),
                    ReadAlongSentence(text: "It isn’t; it’s a question.", start: 490.08, end: 491.38),
                    ReadAlongSentence(text: "When you say \"I can't afford it,\" your brain shuts down.", start: 492.06, end: 495.08),
                    ReadAlongSentence(text: "You stop looking.", start: 495.42, end: 496.3),
                    ReadAlongSentence(text: "You become a victim of the economy rather than a master of it.", start: 496.66, end: 499.58),
                    ReadAlongSentence(text: "The failure here isn't a lack of resources; it's a lack of imagination.", start: 500.26, end: 504.14),
                ]),
                ReadAlongBlock(isHeading: true, sentences: [
                    ReadAlongSentence(text: "The Phoenix Fire Sale", start: 505.44, end: 506.7),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "The author illustrates this with a story from the recession.", start: 507.98, end: 510.42),
                    ReadAlongSentence(text: "While everyone else was screaming that the sky was falling, the author saw a clearance sale.", start: 510.86, end: 515.08),
                    ReadAlongSentence(text: "He recounts finding a house in Phoenix during a market crash.", start: 515.68, end: 518.36),
                    ReadAlongSentence(text: "The home was worth $65,000, but the owner was desperate, and the bank was foreclosing.", start: 518.78, end: 523.9),
                    ReadAlongSentence(text: "The price? $20,000.", start: 524.34, end: 524.82),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "Here is the twist: The author didn’t have $20,000.", start: 526.5, end: 528.98),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "But he didn't let that stop him.", start: 530.36, end: 531.56),
                    ReadAlongSentence(text: "He borrowed the $2,000 down payment from a friend for 90 days for $200 interest.", start: 531.94, end: 536.55),
                    ReadAlongSentence(text: "He bought the house, advertised it, and sold it for $60,000 within a few weeks.", start: 537.15, end: 541.43),
                    ReadAlongSentence(text: "He paid back the friend, paid the bank, and pocketed $40,000.", start: 541.89, end: 544.25),
                    ReadAlongSentence(text: "He didn’t work for that money; he invented it.", start: 545.73, end: 547.95),
                    ReadAlongSentence(text: "He used his financial intelligence to bridge the gap between a seller in pain and a buyer in need.", start: 548.45, end: 553.27),
                ]),
                ReadAlongBlock(isHeading: true, sentences: [
                    ReadAlongSentence(text: "The 2026 Arbitrage", start: 554.53, end: 556.19),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "In 2026, you don't need to drive around Phoenix to find deals; the opportunities are digital and move at the speed of light.", start: 556.55, end: 564.09),
                    ReadAlongSentence(text: "The \"distressed seller\" might be a tech startup liquidating assets, or a mispriced token in a DeFi liquidity pool.", start: 564.63, end: 570.99),
                    ReadAlongSentence(text: "The \"house\" might be an undervalued domain name, a neglected newsletter with 10,000 subscribers, or an inefficiency in an AI workflow that you can automate and sell.", start: 571.59, end: 580.57),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "Today, \"inventing money\" means spotting informational asymmetry.", start: 581.35, end: 584.73),
                    ReadAlongSentence(text: "It’s realizing that Company A needs a specific dataset that Company B gives away for free, and you become the bridge.", start: 585.29, end: 591.39),
                    ReadAlongSentence(text: "It’s using an AI agent to analyze thousands of real estate listings or crypto pairs instantly to find the one that is mispriced.", start: 591.93, end: 598.15),
                    ReadAlongSentence(text: "The principle is the same: You are not buying with cash; you are buying with insight.", start: 598.63, end: 603.03),
                ]),
            ],
            5: [
                ReadAlongBlock(isHeading: true, sentences: [
                    ReadAlongSentence(text: "The Specialist's Curse", start: 617.27, end: 618.59),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "Here is the lie that universities sell you: \"Specialize to succeed.", start: 619.75, end: 623.31),
                    ReadAlongSentence(text: "They tell you to become the best neurosurgeon, the sharpest coder, or the most nuanced accountant.", start: 623.89, end: 628.75),
                    ReadAlongSentence(text: "The friction is that specialization is a form of dependency.", start: 629.47, end: 632.49),
                    ReadAlongSentence(text: "When you know more and more about less and less, you become a cog that only fits into one specific machine.", start: 633.09, end: 639.03),
                    ReadAlongSentence(text: "If that machine breaks (or the industry shifts), you are obsolete.", start: 639.59, end: 642.97),
                    ReadAlongSentence(text: "Most people fail to build wealth because they are too busy protecting their \"career path\" to notice they are walking off a cliff.", start: 643.85, end: 649.74),
                    ReadAlongSentence(text: "They cling to job security, unaware that \"JOB\" is an acronym for \"Just Over Broke.\"", start: 650.5, end: 655.26),
                ]),
                ReadAlongBlock(isHeading: true, sentences: [
                    ReadAlongSentence(text: "The \"Best-Selling\" Author", start: 656.02, end: 657.2),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "The author illustrates this with a stinging encounter with a talented young newspaper reporter.", start: 658.24, end: 662.52),
                    ReadAlongSentence(text: "She had a Master’s degree in English and wrote beautifully, but she was struggling to make a living from her novels.", start: 663.08, end: 668.08),
                    ReadAlongSentence(text: "She asked the author for advice.", start: 668.66, end: 669.96),
                    ReadAlongSentence(text: "His answer?", start: 670.42, end: 670.88),
                    ReadAlongSentence(text: "\"Go take a sales training course.\"", start: 671.4, end: 672.8),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "She was insulted.", start: 673.44, end: 674.48),
                    ReadAlongSentence(text: "She packed her briefcase, snapping, \"I am a serious writer, not a used-car salesman.", start: 674.86, end: 679.4),
                    ReadAlongSentence(text: "The author gently pointed to a book on the table.", start: 680.08, end: 682.06),
                    ReadAlongSentence(text: "He said, \"Look at the cover.", start: 682.46, end: 683.84),
                    ReadAlongSentence(text: "It says 'Best-Selling Author,' not 'Best-Writing Author'.\"", start: 684.26, end: 687.64),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "He explained that the world is full of talented, poor people.", start: 688.32, end: 691.34),
                    ReadAlongSentence(text: "They are one skill away from great wealth.", start: 691.88, end: 693.78),
                    ReadAlongSentence(text: "The reporter was a master of the product (writing) but ignored the system (selling).", start: 694.32, end: 698.86),
                    ReadAlongSentence(text: "The author urges you to seek work for what you will learn, not what you will earn.", start: 699.58, end: 703.42),
                    ReadAlongSentence(text: "He joined the Marine Corps to learn leadership and Xerox to learn sales—skills that became the foundation of his empire.", start: 704.08, end: 709.88),
                ]),
                ReadAlongBlock(isHeading: true, sentences: [
                    ReadAlongSentence(text: "The 2026 \"Full Stack\" Human", start: 710.7, end: 712.88),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "In 2026, the danger of specialization is existential.", start: 713.52, end: 717.74),
                    ReadAlongSentence(text: "AI Agents can now write the code, draft the legal contract, and generate the marketing copy.", start: 718.3, end: 723.06),
                    ReadAlongSentence(text: "If you are just a \"writer\" or just a \"coder,\" you are competing with a bot that costs pennies.", start: 723.78, end: 729.13),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "The \"Wiser\" strategy today is to become a \"Full Stack\" operator.", start: 730.01, end: 733.63),
                    ReadAlongSentence(text: "You don't need to be the best at any one thing; you need to be in the top 25% of three things that rarely go together—like Finance + Design + AI Prompting.", start: 734.13, end: 743.91),
                    ReadAlongSentence(text: "That unique intersection is where your value lies.", start: 744.53, end: 747.15),
                    ReadAlongSentence(text: "The modern wealthy treat their day jobs as paid apprenticeships.", start: 748.03, end: 751.13),
                    ReadAlongSentence(text: "They rotate roles not because they are flaky, but because they are acquiring the infinity stones of business: Management of Cash Flow, Management of Systems, and Management of People.", start: 751.61, end: 761.19),
                ]),
            ],
            6: [
                ReadAlongBlock(isHeading: true, sentences: [
                    ReadAlongSentence(text: "The \"What If\" Paralyzing Agent", start: 776.61, end: 778.29),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "Here is the tragedy: You have the financial literacy.", start: 779.79, end: 782.45),
                    ReadAlongSentence(text: "You understand assets vs. liabilities.", start: 782.87, end: 784.81),
                    ReadAlongSentence(text: "You see the deal.", start: 785.13, end: 785.99),
                    ReadAlongSentence(text: "And then… you freeze.", start: 786.45, end: 787.97),
                    ReadAlongSentence(text: "The friction isn't the market; it’s the voice in your head screaming, \"What if the tenant leaves?", start: 788.43, end: 792.61),
                    ReadAlongSentence(text: "\"What if the crypto market crashes?", start: 792.97, end: 794.39),
                    ReadAlongSentence(text: "\"What if I lose my job?", start: 794.75, end: 795.81),
                    ReadAlongSentence(text: "This is the \"Inner Saboteur.", start: 796.37, end: 797.97),
                    ReadAlongSentence(text: "It turns smart people into cowards and keeps the middle class safely poor.", start: 798.43, end: 802.25),
                    ReadAlongSentence(text: "The problem is that your brain is wired to survive, not to thrive.", start: 802.87, end: 806.11),
                    ReadAlongSentence(text: "It treats a financial risk like a saber-toothed tiger.", start: 806.45, end: 808.91),
                ]),
                ReadAlongBlock(isHeading: true, sentences: [
                    ReadAlongSentence(text: "The Colonel and the Alamo", start: 810.23, end: 811.37),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "The author confronts this fear—the fear of losing money—head-on.", start: 812.75, end: 816.09),
                    ReadAlongSentence(text: "He tells the story of the Texans and the Alamo.", start: 816.59, end: 818.55),
                    ReadAlongSentence(text: "When the Alamo fell, everyone died.", start: 819.09, end: 820.95),
                    ReadAlongSentence(text: "It was a tragic, total defeat.", start: 821.35, end: 822.91),
                    ReadAlongSentence(text: "But Texans didn’t bury their heads in shame.", start: 823.33, end: 825.01),
                    ReadAlongSentence(text: "They shouted, \"Remember the Alamo!\" and used that failure as the fuel to win their independence.", start: 825.35, end: 830.05),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "He contrasts this with the \"Chicken Littles\" of the world—the cynics who run around yelling, \"The sky is falling!\" whenever there is a rumor of a recession.", start: 830.89, end: 837.18),
                    ReadAlongSentence(text: "He explains that cynics criticize, but winners analyze.", start: 837.76, end: 840.74),
                    ReadAlongSentence(text: "Criticism blinds you; analysis opens your eyes.", start: 841.14, end: 843.82),
                    ReadAlongSentence(text: "He tells the story of finding a great real estate deal, only to have a \"smart\" friend talk him out of it because \"prices might drop.", start: 844.36, end: 849.64),
                    ReadAlongSentence(text: "The prices went up, and the friend stayed poor.", start: 850.08, end: 852.02),
                    ReadAlongSentence(text: "The lesson?", start: 853.32, end: 853.72),
                    ReadAlongSentence(text: "\"Don't let the noise of the world drown out the whisper of opportunity.\"", start: 854.26, end: 857.5),
                ]),
                ReadAlongBlock(isHeading: true, sentences: [
                    ReadAlongSentence(text: "The Algorithmic Doomer", start: 858.76, end: 859.82),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "In 2026, the \"Chicken Littles\" have a megaphone.", start: 860.68, end: 863.76),
                    ReadAlongSentence(text: "They are the algorithmic feeds on your phone, optimized to keep you terrified because fear drives engagement.", start: 864.18, end: 869.54),
                    ReadAlongSentence(text: "The \"Inner Saboteur\" is now powered by AI-generated deepfakes and 24/7 \"Crash Coming Soon\" YouTube thumbnails.", start: 870.12, end: 876.8),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "Laziness has also evolved.", start: 877.52, end: 878.86),
                    ReadAlongSentence(text: "Today, laziness doesn't look like sleeping on the couch; it looks like \"busyness.", start: 879.22, end: 883.14),
                    ReadAlongSentence(text: "You are too busy answering emails, checking Slack, and \"researching\" (watching tutorials) to actually do anything.", start: 883.62, end: 889.24),
                    ReadAlongSentence(text: "This is \"productive procrastination.", start: 889.72, end: 891.62),
                    ReadAlongSentence(text: "The modern winner ignores the comment section.", start: 892.28, end: 894.16),
                    ReadAlongSentence(text: "They realize that if an investment feels \"safe\" to the herd, it’s already too late.", start: 894.62, end: 898.58),
                    ReadAlongSentence(text: "The Saboteur tells you to wait for certainty.", start: 899.12, end: 901.08),
                    ReadAlongSentence(text: "The Wiser investor knows certainty is expensive.", start: 901.58, end: 904.1),
                ]),
            ],
            7: [
                ReadAlongBlock(isHeading: true, sentences: [
                    ReadAlongSentence(text: "The Knowledge Coma", start: 918.62, end: 919.46),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "Here is the final, most dangerous trap: You are smarter now.", start: 920.76, end: 924.58),
                    ReadAlongSentence(text: "You have read the Cores.", start: 924.94, end: 925.8),
                    ReadAlongSentence(text: "You understand the math.", start: 926.14, end: 927.16),
                    ReadAlongSentence(text: "And yet, tomorrow morning, you will likely wake up and do exactly what you did today.", start: 927.6, end: 932.0),
                    ReadAlongSentence(text: "This is the \"Knowledge Coma.", start: 932.62, end: 933.94),
                    ReadAlongSentence(text: "The friction isn't ignorance anymore; it is inertia.", start: 934.62, end: 937.48),
                    ReadAlongSentence(text: "Most people collect financial advice like baseball cards—they categorize it, admire it, and show it off, but they never play the game.", start: 938.46, end: 945.47),
                    ReadAlongSentence(text: "They are waiting for the \"perfect time\" or the \"perfect deal,\" unaware that perfection is just procrastination in a tuxedo.", start: 946.07, end: 952.19),
                ]),
                ReadAlongBlock(isHeading: true, sentences: [
                    ReadAlongSentence(text: "The Power of \"I Don't Want\"", start: 953.37, end: 955.11),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "The author shatters this paralysis not with a spreadsheet, but with a piece of paper.", start: 956.23, end: 960.45),
                    ReadAlongSentence(text: "He tells the story of how he found his \"Why\"—the fuel that outlasted his fear.", start: 961.11, end: 965.53),
                    ReadAlongSentence(text: "He didn't start by writing down \"I want to be rich.", start: 966.13, end: 968.41),
                    ReadAlongSentence(text: "That was too vague.", start: 968.81, end: 969.59),
                    ReadAlongSentence(text: "Instead, he sat down and ruthlessly listed what he didn't want.", start: 969.95, end: 973.47),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "\"I don't want to work for someone else for the rest of my life.", start: 974.19, end: 976.53),
                    ReadAlongSentence(text: "\"I don't want to be told when I can go on vacation.", start: 976.95, end: 978.97),
                    ReadAlongSentence(text: "\"I don't want to miss my child’s soccer game because of a conference call.\"", start: 979.41, end: 982.19),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "He explains that this \"List of Don'ts\" created a pressure cooker of emotion.", start: 983.25, end: 987.13),
                    ReadAlongSentence(text: "It wasn't just a wish; it was a rejection of mediocrity.", start: 987.59, end: 990.67),
                    ReadAlongSentence(text: "This deep-seated emotional leverage—the \"Power of Spirit\"—is the only force strong enough to push you through the inevitable obstacles of the first 90 days.", start: 991.11, end: 999.13),
                    ReadAlongSentence(text: "Without a burning \"Why,\" the \"How\" is useless.", start: 999.59, end: 1002.15),
                ]),
                ReadAlongBlock(isHeading: true, sentences: [
                    ReadAlongSentence(text: "The 2026 \"Micro-Step\" Revolution", start: 1003.27, end: 1005.43),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "In 2026, the barrier to entry has collapsed.", start: 1005.81, end: 1009.03),
                    ReadAlongSentence(text: "You don't need $50,000 for a down payment or a license to trade.", start: 1009.49, end: 1013.17),
                    ReadAlongSentence(text: "The \"Launchpad\" today is built on micro-actions.", start: 1013.71, end: 1016.45),
                    ReadAlongSentence(text: "The author couldn't buy 0.0001% of a building in 1997.", start: 1017.02, end: 1020.74),
                    ReadAlongSentence(text: "Today, you can.", start: 1022.1, end: 1023.04),
                    ReadAlongSentence(text: "You can buy fractional shares of real estate, tokenized gold, or a slice of a startup for the price of a latte.", start: 1023.56, end: 1029.18),
                ]),
                ReadAlongBlock(isHeading: false, sentences: [
                    ReadAlongSentence(text: "The modern error is thinking you need to \"launch\" a massive enterprise.", start: 1030.74, end: 1034.32),
                    ReadAlongSentence(text: "You don't.", start: 1034.66, end: 1035.02),
                    ReadAlongSentence(text: "You need to launch a habit.", start: 1035.48, end: 1036.94),
                    ReadAlongSentence(text: "The \"Wiser\" approach uses automation to bypass your willpower entirely.", start: 1037.54, end: 1041.48),
                    ReadAlongSentence(text: "You don't \"decide\" to invest; you set up a smart contract or an auto-debit that invests for you before you even wake up.", start: 1042.04, end: 1048.08),
                    ReadAlongSentence(text: "You build the wealth architecture once, and then you let the machine run.", start: 1048.58, end: 1052.4),
                ]),
            ],
        ],
    ]
}
