import type { Metadata } from "next";
import { Suspense } from "react";
import { PaymentGatewayClient } from "./payment-gateway-client";

export const metadata: Metadata = { title: "Payment" };

export default function PaymentPage() {
  return (
    <Suspense>
      <PaymentGatewayClient />
    </Suspense>
  );
}
