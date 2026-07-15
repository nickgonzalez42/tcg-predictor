using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace API.Data.Migrations
{
    /// <inheritdoc />
    public partial class AddAutoPrice : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<bool>(
                name: "AutoPrice",
                table: "TrackedCards",
                type: "INTEGER",
                nullable: false,
                defaultValue: false);

            // Backfill owned copies: manual prices stay manual (AutoPrice off);
            // price-less rows go auto with a 0 placeholder (a one-time recompute
            // fills real market-at-acquired values); acquired defaults to added.
            migrationBuilder.Sql(
                """
                UPDATE TrackedCards SET AcquiredAt = AddedAt
                 WHERE Kind = 'owned' AND AcquiredAt IS NULL;
                UPDATE TrackedCards SET AutoPrice = (PurchasePrice IS NULL)
                 WHERE Kind = 'owned';
                UPDATE TrackedCards SET PurchasePrice = 0
                 WHERE Kind = 'owned' AND PurchasePrice IS NULL;
                """);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropColumn(
                name: "AutoPrice",
                table: "TrackedCards");
        }
    }
}
