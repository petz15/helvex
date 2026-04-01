import { notFound } from "next/navigation";
import { BillingTestClient } from "./billing-test-client";

export default function BillingDevPage() {
  if (process.env.NODE_ENV === "production") {
    notFound();
  }

  return <BillingTestClient />;
}
