import Link from "next/link";

import { CATEGORIES } from "@/lib/types";

/*
 * Filters are links carrying query params rather than client state, so the
 * board stays a Server Component and every view is a shareable URL.
 */

function Pill({
  href,
  active,
  children,
}: {
  href: string;
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      className={
        active
          ? "chip bg-[#2f6fed] text-white"
          : "chip border border-[#e3e6ea] bg-white text-[#5c6672] hover:bg-[#f2f4f7] dark:border-[#2c333d] dark:bg-[#151a21] dark:text-[#8b95a3] dark:hover:bg-[#1c222b]"
      }
    >
      {children}
    </Link>
  );
}

export function FilterBar({
  category,
  showAll,
}: {
  category?: string;
  showAll: boolean;
}) {
  const base = (params: Record<string, string | undefined>) => {
    const search = new URLSearchParams();
    if (params.category) search.set("category", params.category);
    if (params.all) search.set("all", params.all);
    const qs = search.toString();
    return qs ? `/?${qs}` : "/";
  };

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Pill href={base({ all: showAll ? "1" : undefined })} active={!category}>
        All categories
      </Pill>
      {CATEGORIES.map((c) => (
        <Pill
          key={c}
          href={base({ category: c, all: showAll ? "1" : undefined })}
          active={category === c}
        >
          {c}
        </Pill>
      ))}

      <span className="mx-1 h-4 w-px bg-[#e3e6ea] dark:bg-[#2c333d]" aria-hidden />

      <Pill href={base({ category, all: showAll ? undefined : "1" })} active={showAll}>
        {showAll ? "Showing done & dismissed" : "Show done & dismissed"}
      </Pill>
    </div>
  );
}
