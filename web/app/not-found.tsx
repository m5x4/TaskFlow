import Link from "next/link";

export default function NotFound() {
  return (
    <main className="mx-auto mt-24 max-w-md px-4 text-center">
      <h1 className="text-lg font-semibold">Task not found</h1>
      <p className="muted mt-2 text-sm">
        It may have been removed, or the link may be out of date.
      </p>
      <Link href="/" className="btn-primary mt-5 inline-block">
        Back to board
      </Link>
    </main>
  );
}
