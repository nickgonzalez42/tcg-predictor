import { useLazyGet400ErrorQuery, useLazyGet401ErrorQuery, useLazyGet404ErrorQuery, useLazyGet500ErrorQuery, useLazyGetValidationErrorQuery } from "./errorApi";
import { useState } from "react";

export default function AboutPage() {
  const [validationErrors, setValidationErrors] = useState<string[]>([]);

  const [trigger400Error] = useLazyGet400ErrorQuery();
  const [trigger401Error] = useLazyGet401ErrorQuery();
  const [trigger404Error] = useLazyGet404ErrorQuery();
  const [trigger500Error] = useLazyGet500ErrorQuery();
  const [triggerValidationError] = useLazyGetValidationErrorQuery();

  const getValidationError = async () => {
    try {
      await triggerValidationError().unwrap();
    } catch (error: any) {
      const errorArray = error.message.split(', ');
      setValidationErrors(errorArray);
    }
  }

  return (
    <div>
      <h3>Errors for testing</h3>
      <div className="btn-group">
        <button className="btn" onClick={() => trigger400Error().catch(err => console.log(err))}>Test 400 Error</button>
        <button className="btn" onClick={() => trigger401Error().catch(err => console.log(err))}>Test 401 Error</button>
        <button className="btn" onClick={() => trigger404Error().catch(err => console.log(err))}>Test 404 Error</button>
        <button className="btn" onClick={() => trigger500Error().catch(err => console.log(err))}>Test 500 Error</button>
        <button className="btn" onClick={getValidationError}>Test Validation Error</button>
      </div>
      {validationErrors.length > 0 && (
        <div className="alert">
          <strong>Validation errors</strong>
          <ul>
            {validationErrors.map(err => (
              <li key={err}>{err}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
