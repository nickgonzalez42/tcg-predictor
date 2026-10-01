using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace API.Data.Migrations
{
    /// <inheritdoc />
    public partial class AcquireSourceOnOwned : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<string>(
                name: "Source",
                table: "TrackedCards",
                type: "TEXT",
                nullable: false,
                defaultValue: "pack");

            // Copies from before the pack/paid distinction all carried a cost
            // basis (auto or manual) and fed paid P/L and the S&P benchmark —
            // grandfather them as individual purchases so those numbers don't
            // change out from under existing portfolios. New adds default to
            // pack pulls in the write paths.
            migrationBuilder.Sql(
                "UPDATE TrackedCards SET Source = 'paid' WHERE Kind = 'owned'");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropColumn(
                name: "Source",
                table: "TrackedCards");
        }
    }
}
