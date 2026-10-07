"use client";

import { useEffect, useState } from "react";

// The intro is pure CSS (see .intro in globals.css). This only makes sure it plays once per page
// load: returning to the home page through client-side navigation shows the hero without it.
let played = false;

export function HeroIntro({ children }: { children: React.ReactNode }) {
  const [intro] = useState(() => !played);
  useEffect(() => {
    played = true;
  }, []);
  return <section className={intro ? "hero intro" : "hero"}>{children}</section>;
}
