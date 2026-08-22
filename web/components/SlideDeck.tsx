"use client";

import type { Deck } from "@/lib/types";

interface Props {
  deck: Deck;
  currentSlide: number;
  lastReason: string;
}

export function SlideDeck({ deck, currentSlide, lastReason }: Props) {
  const slide = deck.slides.find((s) => s.id === currentSlide) ?? deck.slides[0];

  return (
    <section className="deck" aria-live="polite">
      <header className="deck__header">
        <div>
          <p className="deck__eyebrow">{deck.title}</p>
          <h1 className="deck__title">{slide.title}</h1>
        </div>
        <span className="deck__counter">
          {slide.id} <span className="deck__counter-sep">/</span> {deck.slides.length}
        </span>
      </header>

      <ul className="deck__bullets">
        {slide.bullets.map((b, i) => (
          <li key={i} className={b.startsWith("BOXED WARNING") ? "is-warning" : undefined}>
            {b}
          </li>
        ))}
      </ul>

      <footer className="deck__footer">
        <span className="deck__citation">{slide.citation}</span>
        {lastReason && <span className="deck__reason">jumped here for: {lastReason}</span>}
      </footer>

      <nav className="deck__dots" aria-label="Slide progress">
        {deck.slides.map((s) => (
          <span
            key={s.id}
            className={`deck__dot${s.id === slide.id ? " is-active" : ""}`}
            title={s.title}
          />
        ))}
      </nav>
    </section>
  );
}
